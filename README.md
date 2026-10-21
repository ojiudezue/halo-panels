# Halo

An opt-in Home Assistant integration that drives the house wall panels (Kiosk Satellite tablets): LED ring indications for safety and security, with priority arbitration, restore and a sleep rule owned here. Announcements and on-screen cards come in slice S2.

Universal Room Automation (URA) uses Halo as one Notification Manager delivery channel: URA decides which rooms, Halo decides what each panel shows. Any automation or script can call the same services.

Plan and build contract: `docs/PLAN.md` (PLAN-REVIEW-2 section is binding).

## Setup

1. Install through HACS (custom repository) and restart.
2. Add the Halo integration. Pick the sleep sensor (suggested: `binary_sensor.ura_presence_coordinator_house_sleeping`).
3. On the Halo entry, "Add panel" once per wall panel: area, bridge LED light (`light.wallpanel_<room>_led`), optional tablet liveness entity, bedroom flag, enabled.

## Services

| Service | Fields | Response |
|---|---|---|
| `halo.indicate` | `area_id` (one or list), `owner`, `id`, `level` (`critical`/`alert`/`warn`/`info`), optional `pattern`, `ttl_s` (default 1800) | `{area_id: accepted \| suppressed_sleep \| no_surface \| unavailable}` |
| `halo.release` | `owner`, optional `id` (absent = every lease of the owner), optional `area_id` | `{released: n}` |
| `halo.release_all` | none (operator override, also `button.halo_release_all`) | `{released: n}` |

Same `owner` + `id` renews a lease. Highest level wins; ties go to the newest lease; lower leases are held and shown again when the higher one ends. When the last lease ends Halo restores the LED to what it was before the first lease (solid colour, or off if it was off/unknown). `info` uses the bridge `notify` effect, which plays once.

While the sleep sensor is on (or unknown, or inside the boot debounce window), only `alert` and `critical` reach the LED; bedroom panels show only `critical`.

## Entities

- `sensor.halo_<area>_indication`: winning level or `idle`; attributes `active` leases, `last_cmd`, `last_cmd_at`, `last_error`.
- `sensor.halo_<area>_status`: `ok` / `led_unavailable` / `ks_unavailable` / `disabled`.
- `switch.halo_led`: master kill. Off = no LED command is sent, ever.
- `number.halo_boot_debounce_s` (default 180, 0 disables).
- `button.halo_release_all`.

## Development

```bash
uv venv -p 3.14 .venv && uv pip install -p .venv/bin/python -r requirements_test.txt
.venv/bin/python -m pytest -q
.venv/bin/python tests/_drill.py          # mutation drills (restore themselves)
.venv/bin/python -m tests._deep_prop 40000  # long hypothesis run
```
