from bdh_sdk import PluginSpec, Setting, Tile
from bdh_sdk.plugins import INT, LIFECYCLE_ON_DEMAND, NETWORK_NONE

spec = PluginSpec(
    id="radiant",
    app="radiant",
    urls="radiant.urls",
    status="radiant.homepage.status",
    # The assistant can read the zone thermostats and change their setpoints (guest limits apply).
    tools="radiant.assistant.tools",
    run_tool="radiant.assistant.run_tool",
    tiles=(
        Tile(
            title="Radiant Heat",
            url_name="radiant",
            icon="radiant/hub/icon.svg",
            section="lodge",
            subtitle="Floor heating by zone",
            order=40,
        ),
    ),
    settings=(
        Setting(
            "setpoint_debounce_s",
            "Setpoint debounce",
            type=INT,
            default=10,
            minimum=0,
            maximum=120,
            unit="s",
            help="How long the page waits after the last +/- tap before sending the setpoint "
                 "(the Nest integration is rate limited).",
        ),
        Setting(
            "guest_min_c",
            "Assistant: guest minimum",
            type=INT,
            default=18,
            minimum=10,
            maximum=32,
            unit="°C",
            help="Lowest setpoint the voice assistant sets for guests (and between stays).",
        ),
        Setting(
            "guest_max_c",
            "Assistant: guest maximum",
            type=INT,
            default=24,
            minimum=10,
            maximum=32,
            unit="°C",
            help="Highest setpoint the voice assistant sets for guests (and between stays).",
        ),
        Setting(
            "guest_max_change_c",
            "Assistant: guest change per request",
            type=INT,
            default=3,
            minimum=0,
            maximum=10,
            unit="°C",
            help="Most the assistant moves a guest's setpoint in one request. Signed-in owners "
                 "get the thermostat's full range.",
        ),
    ),
    api_version=2,
    permissions=(
        "ha.read:sensor.usl_environmental_temperature_3",
        "ha.read:input_boolean.home_away_mode_enabled",
        "ha.read:sensor.aeco_1988_*",
        "ha.read:binary_sensor.aeco_1988_*",
        "ha.read:switch.aeco_1988_*",
        "ha.read:sensor.thermux_*",
        "ha.read:sensor.mechanical_room_*",
        "ha.read:binary_sensor.mechanical_room_*",
        "ha.read:climate.aeco_1988_climate",
        "ha.read:climate.master_bedroom",
        "ha.read:climate.main_floor",
        "ha.read:climate.ski_room",
        "ha.read:climate.basement_master",
        "ha.read:climate.basement_bunk_rooms",
        "ha.call:climate.set_temperature",
    ),
    network=NETWORK_NONE,
    # No background work: the debounce runs in the browser. The tile status is
    # read live from HA, so wake every 5 minutes to keep it fresh while idle.
    lifecycle=LIFECYCLE_ON_DEMAND,
    wake_every=300,
    memory_mb=96,
)
