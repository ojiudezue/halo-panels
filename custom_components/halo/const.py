"""Constants for Halo.

Numbers-get-knobs ladder (see docs/PLAN.md "Knobs (S1)"):
- Module constants here change only by reviewed code change (safety signalling,
  life-safety floors, protocol bounds).
- Live knobs are entities: switch.halo_led (master kill), number.halo_boot_debounce_s.
- Per-panel structure lives in the config flow (panel subentries).
"""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "halo"

# --- config entry / subentry keys ---
CONF_SLEEP_ENTITY: Final = "sleep_entity"
SUBENTRY_TYPE_PANEL: Final = "panel"
CONF_AREA_ID: Final = "area_id"
CONF_LED_ENTITY: Final = "led_entity"
CONF_KS_ENTITY: Final = "ks_entity"
CONF_BEDROOM: Final = "bedroom"
CONF_ENABLED: Final = "enabled"

# Suggested default only (PLAN-REVIEW-2): the live id was confirmed 2026-10-20 via
# the HA entity registry; it is offered as the selector default, never hard-coded.
SUGGESTED_SLEEP_ENTITY: Final = "binary_sensor.ura_presence_coordinator_house_sleeping"

# --- levels ---
LEVEL_INFO: Final = "info"
LEVEL_WARN: Final = "warn"
LEVEL_ALERT: Final = "alert"
LEVEL_CRITICAL: Final = "critical"
LEVEL_RANK: Final[dict[str, int]] = {
    LEVEL_INFO: 1,
    LEVEL_WARN: 2,
    LEVEL_ALERT: 3,
    LEVEL_CRITICAL: 4,
}
LEVELS: Final = tuple(LEVEL_RANK)

# Effects the bridge LED supports (LedController.kt:46; live effect_list 2026-10-20).
EFFECTS: Final = ("solid", "blink", "pulse", "alert", "notify", "cycle")
# `notify` self-terminates on the bridge (3 flashes then back to its lastSolid,
# LedController.kt:155-158), so a lease shown with it is one-shot (R2-7).
ONE_SHOT_EFFECTS: Final = frozenset({"notify"})

# Rung 1 (review to change): level -> (effect, 4-bit RGB 0-15). Converted x17 on
# send because the bridge stores r*15/255 (R2-7). `alert` ignores colour on the
# bridge (hard-coded red), fine for critical.
LEVEL_EFFECT_MAP: Final[dict[str, tuple[str, tuple[int, int, int]]]] = {
    LEVEL_CRITICAL: ("alert", (15, 0, 0)),
    LEVEL_ALERT: ("pulse", (15, 8, 0)),
    LEVEL_WARN: ("blink", (0, 0, 15)),
    LEVEL_INFO: ("notify", (15, 15, 15)),
}
FOUR_BIT_SCALE: Final = 17

# Rung 1: while the house sleeps only leases at or above these levels reach the LED.
SLEEP_MIN_LEVEL: Final = LEVEL_ALERT
BEDROOM_SLEEP_MIN_LEVEL: Final = LEVEL_CRITICAL

# Rung 1: lease length bounds. ttl_s is optional on halo.indicate.
DEFAULT_TTL_S: Final = 1800
MIN_TTL_S: Final = 10
MAX_TTL_S: Final = 86400

# Rung 1: LED command call timeout (seconds).
LED_CALL_TIMEOUT_S: Final = 5

# Rung 3 default (number.halo_boot_debounce_s). 0 disables the debounce window.
DEFAULT_BOOT_DEBOUNCE_S: Final = 180
MAX_BOOT_DEBOUNCE_S: Final = 1800

# --- services ---
SERVICE_INDICATE: Final = "indicate"
SERVICE_RELEASE: Final = "release"
SERVICE_RELEASE_ALL: Final = "release_all"
ATTR_OWNER: Final = "owner"
ATTR_ID: Final = "id"
ATTR_LEVEL: Final = "level"
ATTR_PATTERN: Final = "pattern"
ATTR_TTL_S: Final = "ttl_s"
ATTR_ANNOUNCE: Final = "announce"
ATTR_CARD: Final = "card"

# --- indicate response values ---
RESULT_ACCEPTED: Final = "accepted"
RESULT_SUPPRESSED_SLEEP: Final = "suppressed_sleep"
RESULT_NO_SURFACE: Final = "no_surface"
RESULT_UNAVAILABLE: Final = "unavailable"

# --- status sensor values ---
STATUS_OK: Final = "ok"
STATUS_LED_UNAVAILABLE: Final = "led_unavailable"
STATUS_KS_UNAVAILABLE: Final = "ks_unavailable"
STATUS_DISABLED: Final = "disabled"
INDICATION_IDLE: Final = "idle"

STORAGE_KEY: Final = "halo.snapshots"
STORAGE_VERSION: Final = 1

SIGNAL_PANEL_UPDATED: Final = "halo_panel_updated_{}"
