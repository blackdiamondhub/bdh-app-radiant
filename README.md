# bdh-app-radiant

Black Diamond Hub app: radiant heating and heat-pump plant dashboard (Home Assistant). Moved out of
[BlackDiamondHub](https://github.com/blackdiamondhub/BlackDiamondHub) and
installed into the hub through the `blackdiamondhub.plugins` entry point
(see [bdh-sdk](https://github.com/blackdiamondhub/bdh-sdk)).

Page: `/radiant/`.

## Container (hub API 2)

Since 2.0.0 the app runs in its own container and reaches Home Assistant only through the hub
broker:

- Permissions: `ha.read` on exactly the plant entities it shows (`sensor.aeco_1988_*`,
  `binary_sensor.aeco_1988_*`, `switch.aeco_1988_*`, `sensor.thermux_*`,
  `sensor.mechanical_room_*`, `binary_sensor.mechanical_room_*`, the six thermostats in
  `config.py`, `sensor.usl_environmental_temperature_3`,
  `input_boolean.home_away_mode_enabled`) and `ha.call:climate.set_temperature` (zone
  setpoints; the view still only accepts the zone thermostats).
- `network: none`, `memory_mb: 96`.
- `lifecycle: on_demand` with `wake_every: 300`. There is no background work (the setpoint
  debounce runs in the browser), so the container only runs while the page is in use. The
  tile status is read live from HA, so the hub wakes the app every 5 minutes to keep it fresh.

Settings (Settings page on the hub, saved per app): `setpoint_debounce_s` (seconds, default 10).
In-process it could also come from `RADIANT_SETPOINT_DEBOUNCE_S` / `BDH_RADIANT_SETPOINT_DEBOUNCE_S`
on the hub; those environment variables are not visible to the container, so set it on the
Settings page if you changed it. No secrets; Home Assistant credentials stay in the hub. The app
has no models, so there is nothing to migrate.

CI's `image` job (from bdh-sdk's `app-ci.yml`) locks the dependencies against the hub's app
runtime, builds the app's image and smoke-tests it the way the supervisor runs it.
## Development

CI (`.github/workflows/ci.yml`) uses the reusable `app-ci.yml` from
bdh-sdk. It builds the wheel, installs it next to the hub, then runs
`hub_smoke`, the app tests and the selenium tests.

To release, bump `version` in `pyproject.toml`. When that lands on
`main`, `release.yml` tags it and publishes the wheel, and the release
notes include the hashed line for the hub's `sites/<site>/apps.txt`.
