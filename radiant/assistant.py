"""Assistant tools: the radiant zone thermostats (the five Nest zones).

``thermostat_status`` reads every zone (or the ones asked for);
``set_thermostat`` changes zone setpoints. Only the zone thermostats in
``config.ZONES`` are ever read or set: never the heat pumps, the AECO plant
controller or any other climate entity, and never a thermostat's mode
(heat/off/eco) or the lodge's away mode.

Outside owner mode (guests, and the dashboard between stays) setpoints stay
within the admin's guest range (``guest_min_c``-``guest_max_c``) and move at
most ``guest_max_change_c`` per request; a zone already outside the range is
never pushed further out, nor pulled the other way.
"""
import difflib
import logging
import re

from bdh_sdk import ALL_MODES
from bdh_sdk.ha import call_service, get_entity_states
from bdh_sdk.plugins import OWNER
from bdh_sdk.settings import app_setting
from bdh_sdk.tools import Tool, ToolError

from . import config as cfg

logger = logging.getLogger(__name__)

APP_ID = "radiant"
STATUS = "thermostat_status"
SET = "set_thermostat"

GUEST_MIN = 18
GUEST_MAX = 24
GUEST_MAX_CHANGE = 3
#: Absolute temperatures in this range are taken as Fahrenheit.
FAHRENHEIT = (50, 90)
#: Up to this many zones a status reading includes the floor loop's return temperature.
MAX_DETAILED = 2

HA_DOWN = "Home Assistant isn't answering right now, so I can't reach the thermostats. Try again in a minute."
UNAVAILABLE = {"unknown", "unavailable", "", None}

# Words that never tell zones apart.
FILLER = frozenset({
    "the", "a", "an", "and", "in", "on", "of", "at", "to", "for", "my", "our", "this", "that", "these",
    "those", "please", "zone", "thermostat", "heat", "heating", "radiant", "floor", "temperature", "temp",
    "set", "area", "level", "it", "is", "up", "down", "warmer", "cooler", "colder", "hotter",
})
QUANTIFIERS = frozenset({"all", "every", "everything", "each", "entire", "whole", "house", "lodge",
                         "home", "everywhere", "place", "both", "thermostats", "zones"})
_SPLIT = re.compile(r",|;|&|\+|/|\band\b|\bplus\b", re.I)

# What guests call each zone, besides its name. Matching ignores FILLER words.
ALIASES = {
    "master_bedroom": ("master bedroom", "upstairs master", "upper master", "upstairs bedroom",
                       "master suite", "primary bedroom", "main bedroom", "top floor"),
    "main_floor": ("main floor", "main", "kitchen", "living room", "living", "dining room", "dining",
                   "great room", "lounge", "family room", "main level", "ground floor"),
    "ski_room": ("ski room", "ski", "boot room", "mudroom", "mud room", "ski locker", "entry", "entrance"),
    "basement_master": ("basement master", "downstairs master", "lower master", "basement bedroom",
                        "downstairs bedroom", "basement master bedroom", "downstairs master bedroom"),
    "basement_bunk_rooms": ("basement bunk", "basement bunk room", "bunk room", "bunk", "bunkroom",
                            "bunk bed", "bunk bedroom", "kid room", "kids room", "basement and bunk"),
}
# Words for a whole floor ("downstairs" is both basement zones).
FLOORS = {
    "upper": ("upstairs", "upper", "upper floor", "top", "second floor"),

    "basement": ("basement", "downstairs", "lower", "lower floor", "lower level"),
}
# Names that fit more than one zone: ask which.
AMBIGUOUS = {
    "master": ("master_bedroom", "basement_master"),
    "bedroom": ("master_bedroom", "basement_master", "basement_bunk_rooms"),

}


def _stem(word):
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def _words(text):
    text = text.casefold().replace("'", "")
    raw = (_stem(w) for w in re.split(r"[^0-9a-z]+", text) if w)
    return tuple(w for w in raw if w not in FILLER)


def _key(text):
    return frozenset(w for w in _words(text) if w not in QUANTIFIERS)


def _zone_keys():
    return {z["id"]: {_key(z["name"]), _key(z["id"]), *(_key(a) for a in ALIASES.get(z["id"], ()))}
            for z in cfg.ZONES}


def _by_id(ids):
    ids = set(ids)
    return [z for z in cfg.ZONES if z["id"] in ids]


def _names(zones):
    return _join([z["name"] for z in zones])


def _join(parts):
    parts = list(parts)
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def _no_match(text):
    return ToolError(f"There's no thermostat zone called {text.strip()!r}. The zones are "
                     f"{_names(cfg.ZONES)}.")


def _ambiguous(text, ids):
    return ToolError(f"{text.strip()!r} could mean {_join([z['name'] for z in _by_id(ids)])}. "
                     "Ask which, or say all of them.")


def _exact(text):
    """The zones ``text`` names exactly (by name, alias or floor), or None."""
    words = _words(text)
    key = frozenset(w for w in words if w not in QUANTIFIERS)
    if not key:
        return list(cfg.ZONES) if words or not text.strip() else None
    keys = _zone_keys()
    hits = [zid for zid, ks in keys.items() if key in ks]
    if hits:
        return _by_id(hits)
    for floor, aliases in FLOORS.items():
        if key in {_key(floor), *(_key(a) for a in aliases)}:
            return [z for z in cfg.ZONES if z["floor"] == floor]
    for phrase, ids in AMBIGUOUS.items():
        if key == _key(phrase):
            raise _ambiguous(text, ids)
    return None


def _loose(text):
    """Zones for a misheard or partial name ("bunk rum", "master bed"), or None."""
    key = _key(text)
    if not key:
        return None
    keys = _zone_keys()
    # Every word the request has is part of one zone's name or aliases.
    within = [zid for zid, ks in keys.items() if any(key <= k for k in ks)]
    if len(within) == 1:
        return _by_id(within)
    if len(within) > 1:
        raise _ambiguous(text, within)
    phrases = {" ".join(sorted(k)): zid for zid, ks in keys.items() for k in ks if k}
    close = difflib.get_close_matches(" ".join(sorted(key)), list(phrases), n=3, cutoff=0.75)
    found = list(dict.fromkeys(phrases[c] for c in close))
    if len(found) == 1:
        return _by_id(found)
    if found:
        raise _ambiguous(text, found)
    return None


def resolve(text):
    """The zones a request names, in page order. Raises ToolError for none or several possible."""
    text = text or ""
    found = _exact(text)
    if found is None:
        parts = [p for p in _SPLIT.split(text) if p.strip()]
        if len(parts) < 2:
            found = _loose(text)
            if found is None:
                raise _no_match(text)
        else:
            found = []
            for part in parts:
                got = _exact(part)
                if got is None:
                    got = _loose(part)
                if got is None:
                    raise _no_match(part)
                found += got
    ids = {z["id"] for z in found}
    return [z for z in cfg.ZONES if z["id"] in ids]


# ── Readings ──────────────────────────────────────────────────────────────

def _states(zones):
    ids = [z["climate"] for z in zones] + [z["return"] for z in zones] + [cfg.AWAY_MODE]
    try:
        states = get_entity_states(ids)
    except Exception as exc:  # the broker raises when HA or the hub is down
        logger.warning("Thermostat states for the assistant failed: %s", exc)
        raise ToolError(HA_DOWN) from None
    if not states or not any(z["climate"] in states for z in zones):
        raise ToolError(HA_DOWN)
    return states


def _num(value):
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _deg(value):
    """21.0 -> "21°", 20.5 -> "20.5°"."""
    value = round(value * 2) / 2
    return f"{value:g}°"


def _reading(states, zone):
    s = states.get(zone["climate"]) or {}
    attrs = s.get("attributes") or {}
    mode = s.get("state")
    ret = states.get(zone["return"]) or {}
    return {
        "mode": "unavailable" if mode in UNAVAILABLE else mode,
        "cur": _num(attrs.get("current_temperature")),
        "tgt": _num(attrs.get("temperature")),
        "low": _num(attrs.get("target_temp_low")),
        "high": _num(attrs.get("target_temp_high")),
        "eco": attrs.get("preset_mode") == "eco",
        "action": attrs.get("hvac_action"),
        "floor": None if ret.get("state") in UNAVAILABLE else _num(ret.get("state")),
        "min": _num(attrs.get("min_temp")) or cfg.SETPOINT_MIN,
        "max": _num(attrs.get("max_temp")) or cfg.SETPOINT_MAX,
    }


def _status_text(zone, r, detailed):
    name = zone["name"]
    if r["mode"] == "unavailable":
        return f"{name}: not responding."
    parts = [f"{name} {_deg(r['cur'])}" if r["cur"] is not None else name]
    if r["mode"] == "off":
        parts.append("heat off")
    elif r["tgt"] is not None:
        parts.append(f"set {_deg(r['tgt'])}" + (" (eco)" if r["eco"] else ""))
    elif r["low"] is not None and r["high"] is not None:
        parts.append(f"set {_deg(r['low'])}-{_deg(r['high'])}" + (" (eco)" if r["eco"] else ""))
    if r["mode"] == "cool":
        parts.append("cooling mode")
    if r["action"] in ("heating", "cooling"):
        parts.append(r["action"])
    if detailed and r["floor"] is not None:
        parts.append(f"floor loop {_deg(r['floor'])}")
    return ", ".join(parts) + "."


def _status(args):
    zones = resolve(args.get("zones") or "all")
    states = _states(zones)
    detailed = len(zones) <= MAX_DETAILED
    lines = [_status_text(z, _reading(states, z), detailed) for z in zones]
    if (states.get(cfg.AWAY_MODE) or {}).get("state") == "on":
        lines.append("Away mode is on.")
    return " ".join(lines)


# ── Setting ───────────────────────────────────────────────────────────────

def _int_setting(key, default):
    try:
        return int(app_setting(APP_ID, key, default, cast=float))
    except (TypeError, ValueError):
        return default


def guest_limits():
    """(min, max, max change) for guest and dashboard sessions, from the app's settings."""
    low = _int_setting("guest_min_c", GUEST_MIN)
    high = _int_setting("guest_max_c", GUEST_MAX)
    step = _int_setting("guest_max_change_c", GUEST_MAX_CHANGE)
    low = max(int(cfg.SETPOINT_MIN), min(low, int(cfg.SETPOINT_MAX)))
    high = max(low, min(high, int(cfg.SETPOINT_MAX)))
    return low, high, max(0, step)


def _snap(value):
    return round(value / cfg.SETPOINT_STEP) * cfg.SETPOINT_STEP


def plan(current, *, temperature=None, change=None, owner=False, limits=None, zone_min=None, zone_max=None):
    """``(target, limit)`` for one zone: the setpoint to send and why it was cut short, if it was.

    ``limit`` is None, "max" (the most allowed), "min" (the least) or
    "step" (the biggest change allowed at once).
    """
    wanted = current + change if temperature is None else temperature
    wanted = _snap(wanted)
    low = max(cfg.SETPOINT_MIN, zone_min if zone_min is not None else cfg.SETPOINT_MIN)
    high = min(cfg.SETPOINT_MAX, zone_max if zone_max is not None else cfg.SETPOINT_MAX)
    limit = None
    if not owner:
        g_low, g_high, g_step = limits or guest_limits()
        # Never push a zone further out of the guest range, nor pull it the other way.
        low, high = max(low, min(g_low, current)), min(high, max(g_high, current))
        if wanted > current + g_step and current + g_step < high:
            wanted, limit = _snap(current + g_step), "step"
        elif wanted < current - g_step and current - g_step > low:
            wanted, limit = _snap(current - g_step), "step"
    if wanted > high:
        wanted, limit = _snap(high), "max"
    elif wanted < low:
        wanted, limit = _snap(low), "min"
    return wanted, limit


def _celsius(temperature):
    if FAHRENHEIT[0] <= temperature <= FAHRENHEIT[1]:
        return (temperature - 32) * 5 / 9
    return temperature


LIMIT_TEXT = {
    "max": "the most I can do",
    "min": "the lowest I can go",
    "step": "the most I can change it at once",
}


def _set(args, mode):
    temperature, change = args.get("temperature"), args.get("change")
    if (temperature is None) == (change is None):
        raise ToolError("Say either a temperature to set (in °C) or how many degrees warmer or cooler.")
    if change is not None and change == 0:
        raise ToolError("Say how many degrees warmer (positive) or cooler (negative).")
    owner = mode == OWNER
    limits = None if owner else guest_limits()
    if temperature is not None:
        temperature = _celsius(temperature)
    zones = resolve(args["zones"])
    states = _states(zones)
    done, same, skipped, failed = [], [], [], []
    for zone in zones:
        r = _reading(states, zone)
        if r["mode"] == "unavailable":
            skipped.append(f"{zone['name']} isn't responding")
            continue
        if r["mode"] == "off":
            skipped.append(f"{zone['name']} heat is turned off")
            continue
        if r["tgt"] is None:
            why = "is in eco mode" if r["eco"] else "has a heat-cool range"
            skipped.append(f"{zone['name']} {why}, so it can't take a single setpoint")
            continue
        target, limit = plan(r["tgt"], temperature=temperature, change=change, owner=owner, limits=limits,
                             zone_min=r["min"], zone_max=r["max"])
        if target == r["tgt"]:
            same.append((zone, target, limit))
            continue
        try:
            ok, err = call_service("climate", "set_temperature", zone["climate"], {"temperature": target})
        except Exception as exc:
            logger.warning("Setpoint %s -> %s failed: %s", zone["climate"], target, exc)
            raise ToolError(HA_DOWN) from None
        if not ok:
            logger.warning("Setpoint %s -> %s failed: %s", zone["climate"], target, err)
            failed.append(zone)
            continue
        logger.info("Radiant (assistant, %s): set %s from %.1f to %.1f", mode, zone["climate"], r["tgt"], target)
        done.append((zone, target, limit))
    return _set_text(done, same, skipped, failed)


def _grouped(items, verb):
    """"Main Floor and Ski Room set to 21°, the most I can do." per distinct (target, limit)."""
    out = []
    for key in dict.fromkeys((t, lim) for _, t, lim in items):
        zones = [z for z, t, lim in items if (t, lim) == key]
        text = f"{_names(zones)} {verb(len(zones)) if callable(verb) else verb} {_deg(key[0])}"
        if key[1]:
            text += f", {LIMIT_TEXT[key[1]]}"
        out.append(text + ".")
    return out


def _set_text(done, same, skipped, failed):
    lines = _grouped(done, "set to")
    if same:
        lines += _grouped(same, lambda n: "is already at" if n == 1 else "are already at")
    if skipped:
        lines.append(_join(skipped)[:1].upper() + _join(skipped)[1:] + ".")
    if failed:
        lines.append(f"{_names(failed)} didn't respond.")
    if not done and not same and not failed and not skipped:
        lines.append("Nothing to change.")
    return " ".join(lines)


# ── Hooks ─────────────────────────────────────────────────────────────────

def _zones_param(required):
    names = ", ".join(z["name"] for z in cfg.ZONES)
    return {"type": "string",
            "description": f"Which zones, in the guest's words: a zone ({names}), a room (kitchen and "
                           "living room are Main Floor; bunk room is Basement + Bunks), a floor "
                           "(upstairs, downstairs), 'all', or several separated by commas."
                           + ("" if required else " Leave it out for every zone.")}


def tools(mode=None):
    owner = mode == OWNER
    low, high, step = (cfg.SETPOINT_MIN, cfg.SETPOINT_MAX, None) if owner else guest_limits()
    limits = f"{low:g}-{high:g} °C" + ("" if step is None else f", at most {step} degrees per request")
    return [
        Tool(
            id=STATUS,
            title="Thermostat status",
            description="Read the in-floor radiant heat thermostats: each zone's room temperature, its "
                        "setpoint and whether it is heating. Use it for how warm a room is or what the "
                        "thermostats are set to. Temperatures are °C.",
            params={"type": "object", "properties": {"zones": _zones_param(False)}},
            modes=ALL_MODES,
            readonly=True,
        ),
        Tool(
            id=SET,
            title="Set thermostat",
            description="Change room thermostat setpoints (radiant floor heat). Give temperature to set an "
                        "exact °C, or change for warmer/cooler: warmer +1, a bit +1, a couple degrees "
                        f"+2, cooler -1. Allowed: {limits}; it says when it had to stop short.",
            params={"type": "object", "properties": {
                "zones": _zones_param(True),
                "temperature": {"type": "number", "minimum": 5, "maximum": 90,
                                "description": "Setpoint in °C (e.g. 22). Fahrenheit values 50-90 are "
                                               "converted."},
                "change": {"type": "number", "minimum": -10, "maximum": 10,
                           "description": "Degrees °C to move the current setpoint: positive warmer, "
                                          "negative cooler."},
            }, "required": ["zones"]},
            modes=ALL_MODES,
        ),
    ]


def run_tool(tool_id, args, mode=None):
    if tool_id == STATUS:
        return _status(args)
    if tool_id == SET:
        return _set(args, mode)
    raise ToolError(f"Unknown tool {tool_id!r}")
