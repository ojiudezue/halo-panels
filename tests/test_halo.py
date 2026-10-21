"""Behavioural tests for Halo S1a. Every ordering assertion reads the LED command log.

Expected commands are hard-coded literals (independent oracle), not read from
LEVEL_EFFECT_MAP: a mutated map must turn these RED.
"""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from custom_components.halo.const import DOMAIN, STORAGE_KEY

from .conftest import (
    KITCHEN_KS,
    KITCHEN_LED,
    MASTER_LED,
    SLEEP,
    LedLog,
    indicate,
    make_entry,
    release,
    setup_halo,
)

CRIT = ("turn_on", {"effect": "alert", "rgb_color": [255, 0, 0]})
ALERT = ("turn_on", {"effect": "pulse", "rgb_color": [255, 136, 0]})
WARN = ("turn_on", {"effect": "blink", "rgb_color": [0, 0, 255]})
INFO = ("turn_on", {"effect": "notify", "rgb_color": [255, 255, 255]})
OFF = ("turn_off", {})


def _norm(cmds):
    out = []
    for svc, data in cmds:
        d = dict(data)
        if "rgb_color" in d:
            d["rgb_color"] = list(d["rgb_color"])
        out.append((svc, d))
    return out


# ------------------------------------------------------------- preemption


async def test_higher_level_preempts(hass: HomeAssistant, led_log: LedLog) -> None:
    await setup_halo(hass)
    m = led_log.mark()
    await indicate(hass, "kitchen", "w", "warn")
    await indicate(hass, "kitchen", "a", "alert")
    assert _norm(led_log.since(m, KITCHEN_LED)) == [WARN, ALERT]


async def test_lower_held_not_shown(hass: HomeAssistant, led_log: LedLog) -> None:
    """Plan acceptance: alert then warn -> no blue; release alert -> blue; release warn -> restore."""
    await setup_halo(hass)
    m = led_log.mark()
    await indicate(hass, "kitchen", "a", "alert")
    await indicate(hass, "kitchen", "w", "warn")
    assert _norm(led_log.since(m, KITCHEN_LED)) == [ALERT]
    await release(hass, "a")
    assert _norm(led_log.since(m, KITCHEN_LED)) == [ALERT, WARN]
    await release(hass, "w")
    assert _norm(led_log.since(m, KITCHEN_LED)) == [ALERT, WARN, OFF]


async def test_out_of_order_release(hass: HomeAssistant, led_log: LedLog) -> None:
    """Two criticals: releasing the older one sends nothing; releasing the last restores."""
    await setup_halo(hass)
    m = led_log.mark()
    await indicate(hass, "kitchen", "c1", "critical")
    await indicate(hass, "kitchen", "c2", "critical")
    await indicate(hass, "kitchen", "w", "warn")
    await release(hass, "c1")
    assert _norm(led_log.since(m, KITCHEN_LED)) == [CRIT]
    await release(hass, "c2")
    await release(hass, "w")
    assert _norm(led_log.since(m, KITCHEN_LED)) == [CRIT, WARN, OFF]


async def test_tie_goes_to_newest_lease(hass: HomeAssistant, led_log: LedLog) -> None:
    await setup_halo(hass)
    m = led_log.mark()
    await indicate(hass, "kitchen", "a1", "alert", pattern="blink")
    await indicate(hass, "kitchen", "a2", "alert")
    assert _norm(led_log.since(m, KITCHEN_LED)) == [
        ("turn_on", {"effect": "blink", "rgb_color": [255, 136, 0]}),
        ALERT,
    ]
    # releasing the newer reverts to the older one of the same level
    await release(hass, "a2")
    assert _norm(led_log.since(m, KITCHEN_LED))[-1] == (
        "turn_on",
        {"effect": "blink", "rgb_color": [255, 136, 0]},
    )


async def test_renewal_does_not_resend(hass: HomeAssistant, led_log: LedLog) -> None:
    await setup_halo(hass)
    m = led_log.mark()
    await indicate(hass, "kitchen", "w", "warn")
    await indicate(hass, "kitchen", "w", "warn")
    assert _norm(led_log.since(m, KITCHEN_LED)) == [WARN]


# ------------------------------------------------------------ restore / snapshot


async def test_restore_from_unknown_is_off(hass: HomeAssistant, led_log: LedLog) -> None:
    await setup_halo(hass, kitchen_led="unknown")
    m = led_log.mark()
    await indicate(hass, "kitchen", "w", "warn")
    await release(hass, "w")
    assert _norm(led_log.since(m, KITCHEN_LED)) == [WARN, OFF]


async def test_restore_to_prior_solid_colour(hass: HomeAssistant, led_log: LedLog) -> None:
    hass.states.async_set(KITCHEN_LED, "on", {"rgb_color": (0, 255, 0)})
    await setup_halo(hass)
    m = led_log.mark()
    await indicate(hass, "kitchen", "w", "warn")
    await release(hass, "w")
    assert _norm(led_log.since(m, KITCHEN_LED)) == [
        WARN,
        ("turn_on", {"effect": "solid", "rgb_color": [0, 255, 0]}),
    ]


async def test_snapshot_not_taken_from_own_command(
    hass: HomeAssistant, led_log: LedLog
) -> None:
    """Second lease arriving mid-indication must not re-snapshot Halo's own blue."""
    await setup_halo(hass)  # LED starts off
    m = led_log.mark()
    await indicate(hass, "kitchen", "w", "warn")  # LED entity now on, blue
    await indicate(hass, "kitchen", "a", "alert")
    await release(hass, "a")
    await release(hass, "w")
    assert _norm(led_log.since(m, KITCHEN_LED))[-1] == OFF


async def test_snapshot_survives_reload_mid_lease(
    hass: HomeAssistant, led_log: LedLog
) -> None:
    """Reload mid-lease: unload restores from the ORIGINAL snapshot, not the blue."""
    hass.states.async_set(KITCHEN_LED, "on", {"rgb_color": (0, 255, 0)})
    entry = await setup_halo(hass)
    await indicate(hass, "kitchen", "w", "warn")
    m = led_log.mark()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert _norm(led_log.since(m, KITCHEN_LED)) == [
        ("turn_on", {"effect": "solid", "rgb_color": [0, 255, 0]})
    ]


async def test_setup_with_stale_snapshot_restores(
    hass: HomeAssistant, led_log: LedLog, hass_storage
) -> None:
    hass_storage[STORAGE_KEY] = {
        "version": 1,
        "key": STORAGE_KEY,
        "data": {"sub_kitchen": {"state": "on", "rgb_color": [0, 255, 0]}},
    }
    hass.states.async_set(KITCHEN_LED, "on", {"rgb_color": (0, 0, 255)})
    m = led_log.mark()
    await setup_halo(hass)
    assert _norm(led_log.since(m, KITCHEN_LED)) == [
        ("turn_on", {"effect": "solid", "rgb_color": [0, 255, 0]})
    ]
    assert _norm(led_log.since(m, MASTER_LED)) == []
    data = await Store(hass, 1, STORAGE_KEY).async_load()
    assert data == {}


async def test_snapshot_persisted_while_active(hass: HomeAssistant, led_log: LedLog) -> None:
    hass.states.async_set(KITCHEN_LED, "on", {"rgb_color": (0, 255, 0)})
    await setup_halo(hass)
    await indicate(hass, "kitchen", "w", "warn")
    data = await Store(hass, 1, STORAGE_KEY).async_load()
    assert data == {"sub_kitchen": {"state": "on", "rgb_color": [0, 255, 0]}}


async def test_unload_restores_and_cancels_timers(
    hass: HomeAssistant, led_log: LedLog, freezer: FrozenDateTimeFactory
) -> None:
    entry = await setup_halo(hass)
    await indicate(hass, "kitchen", "w", "warn", ttl_s=60)
    m = led_log.mark()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert _norm(led_log.since(m, KITCHEN_LED)) == [OFF]
    freezer.tick(timedelta(seconds=120))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert _norm(led_log.since(m, KITCHEN_LED)) == [OFF]


async def test_unload_removes_listeners(hass: HomeAssistant, led_log: LedLog) -> None:
    entry = await setup_halo(hass)
    await indicate(hass, "kitchen", "w", "warn")
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    m = led_log.mark()
    hass.states.async_set(SLEEP, "on")
    hass.states.async_set(KITCHEN_LED, "unavailable")
    hass.states.async_set(KITCHEN_LED, "off")
    await hass.async_block_till_done()
    assert led_log.since(m, KITCHEN_LED) == []


async def test_unload_with_led_unavailable_keeps_snapshot(
    hass: HomeAssistant, led_log: LedLog
) -> None:
    entry = await setup_halo(hass)
    await indicate(hass, "kitchen", "w", "warn")
    hass.states.async_set(KITCHEN_LED, "unavailable")
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    data = await Store(hass, 1, STORAGE_KEY).async_load()
    assert data == {"sub_kitchen": {"state": "off", "rgb_color": None}}


# ----------------------------------------------------------------- TTL


async def test_ttl_expiry_restores(
    hass: HomeAssistant, led_log: LedLog, freezer: FrozenDateTimeFactory
) -> None:
    await setup_halo(hass)
    m = led_log.mark()
    await indicate(hass, "kitchen", "w", "warn", ttl_s=60)
    freezer.tick(timedelta(seconds=59))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert _norm(led_log.since(m, KITCHEN_LED)) == [WARN]
    freezer.tick(timedelta(seconds=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert _norm(led_log.since(m, KITCHEN_LED)) == [WARN, OFF]


async def test_ttl_expiry_of_higher_reveals_lower(
    hass: HomeAssistant, led_log: LedLog, freezer: FrozenDateTimeFactory
) -> None:
    await setup_halo(hass)
    m = led_log.mark()
    await indicate(hass, "kitchen", "w", "warn", ttl_s=600)
    await indicate(hass, "kitchen", "a", "alert", ttl_s=60)
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert _norm(led_log.since(m, KITCHEN_LED)) == [WARN, ALERT, WARN]


async def test_renew_extends_ttl(
    hass: HomeAssistant, led_log: LedLog, freezer: FrozenDateTimeFactory
) -> None:
    await setup_halo(hass)
    m = led_log.mark()
    await indicate(hass, "kitchen", "w", "warn", ttl_s=60)
    freezer.tick(timedelta(seconds=50))
    async_fire_time_changed(hass)
    await indicate(hass, "kitchen", "w", "warn", ttl_s=60)
    freezer.tick(timedelta(seconds=20))  # past the first expiry
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert _norm(led_log.since(m, KITCHEN_LED)) == [WARN]
    freezer.tick(timedelta(seconds=45))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert _norm(led_log.since(m, KITCHEN_LED)) == [WARN, OFF]


async def test_release_cancels_ttl_timer(
    hass: HomeAssistant, led_log: LedLog, freezer: FrozenDateTimeFactory
) -> None:
    """Release then re-indicate the same id with a longer TTL: the old timer must not fire."""
    await setup_halo(hass)
    m = led_log.mark()
    await indicate(hass, "kitchen", "w", "warn", ttl_s=60)
    await release(hass, "w")
    await indicate(hass, "kitchen", "w", "warn", ttl_s=600)
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert _norm(led_log.since(m, KITCHEN_LED)) == [WARN, OFF, WARN]


# ------------------------------------------------------------------- one-shot


async def test_info_is_one_shot(
    hass: HomeAssistant, led_log: LedLog, freezer: FrozenDateTimeFactory
) -> None:
    await setup_halo(hass)
    m = led_log.mark()
    await indicate(hass, "kitchen", "i", "info", ttl_s=60)
    assert hass.states.get("sensor.kitchen_halo_indication").state == "info"
    # an unrelated step must not replay the notify
    hass.states.async_set(SLEEP, "off", {"x": 1})
    await indicate(hass, "master", "x", "warn")
    assert _norm(led_log.since(m, KITCHEN_LED)) == [INFO]
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert _norm(led_log.since(m, KITCHEN_LED)) == [INFO, OFF]
    assert hass.states.get("sensor.kitchen_halo_indication").state == "idle"


async def test_info_under_released_higher_restores(
    hass: HomeAssistant, led_log: LedLog
) -> None:
    """A consumed one-shot must not leave a released higher effect running."""
    await setup_halo(hass)
    m = led_log.mark()
    await indicate(hass, "kitchen", "i", "info")
    await indicate(hass, "kitchen", "w", "warn")
    await release(hass, "w")
    assert _norm(led_log.since(m, KITCHEN_LED)) == [INFO, WARN, OFF]
    await release(hass, "i")
    assert _norm(led_log.since(m, KITCHEN_LED)) == [INFO, WARN, OFF]


async def test_effect_map_round_trips_4bit(hass: HomeAssistant, led_log: LedLog) -> None:
    """Every channel sent is a multiple of 17 (bridge stores r*15/255)."""
    await setup_halo(hass)
    for i, level in enumerate(["info", "warn", "alert", "critical"]):
        await indicate(hass, "kitchen", f"l{i}", level)
    rgbs = [d["rgb_color"] for s, d in led_log.for_led(KITCHEN_LED) if "rgb_color" in d]
    assert rgbs
    for rgb in rgbs:
        for c in rgb:
            assert c % 17 == 0 and (c // 17) * 15 // 15 == c // 17


# ---------------------------------------------------------------------- sleep


async def test_sleep_floor(hass: HomeAssistant, led_log: LedLog) -> None:
    await setup_halo(hass, sleeping="on")
    m = led_log.mark()
    resp = await indicate(hass, "kitchen", "w", "warn")
    assert resp == {"kitchen": "suppressed_sleep"}
    assert led_log.since(m, KITCHEN_LED) == []
    resp = await indicate(hass, "kitchen", "a", "alert")
    assert resp == {"kitchen": "accepted"}
    assert _norm(led_log.since(m, KITCHEN_LED)) == [ALERT]


async def test_bedroom_sleep_floor(hass: HomeAssistant, led_log: LedLog) -> None:
    await setup_halo(hass, sleeping="on")
    m = led_log.mark()
    resp = await indicate(hass, "master", "a", "alert")
    assert resp == {"master": "suppressed_sleep"}
    assert led_log.since(m, MASTER_LED) == []
    resp = await indicate(hass, "master", "c", "critical")
    assert resp == {"master": "accepted"}
    assert _norm(led_log.since(m, MASTER_LED)) == [CRIT]


async def test_bedroom_flag_only_on_bedroom(hass: HomeAssistant, led_log: LedLog) -> None:
    """Non-bedroom panel shows alert while sleeping (bedroom rule is per panel)."""
    await setup_halo(hass, sleeping="on")
    m = led_log.mark()
    resp = await indicate(hass, ["kitchen", "master"], "a", "alert")
    assert resp == {"kitchen": "accepted", "master": "suppressed_sleep"}
    assert _norm(led_log.since(m, KITCHEN_LED)) == [ALERT]
    assert led_log.since(m, MASTER_LED) == []


async def test_sleep_onset_demotes_running_warn(
    hass: HomeAssistant, led_log: LedLog
) -> None:
    await setup_halo(hass)
    m = led_log.mark()
    await indicate(hass, "kitchen", "w", "warn")
    hass.states.async_set(SLEEP, "on")
    await hass.async_block_till_done()
    assert _norm(led_log.since(m, KITCHEN_LED)) == [WARN, OFF]


async def test_sleep_exit_reshows_held(hass: HomeAssistant, led_log: LedLog) -> None:
    await setup_halo(hass, sleeping="on")
    m = led_log.mark()
    await indicate(hass, "kitchen", "w", "warn")
    hass.states.async_set(SLEEP, "off")
    await hass.async_block_till_done()
    assert _norm(led_log.since(m, KITCHEN_LED)) == [WARN]


async def test_sleep_onset_keeps_alert(hass: HomeAssistant, led_log: LedLog) -> None:
    await setup_halo(hass)
    m = led_log.mark()
    await indicate(hass, "kitchen", "w", "warn")
    await indicate(hass, "kitchen", "a", "alert")
    hass.states.async_set(SLEEP, "on")
    await hass.async_block_till_done()
    await release(hass, "a")
    # alert stays through sleep onset; on its release the held warn is below
    # the floor, so the restore goes out instead of blue
    assert _norm(led_log.since(m, KITCHEN_LED)) == [WARN, ALERT, OFF]


async def test_sleep_unknown_applies_floor(hass: HomeAssistant, led_log: LedLog) -> None:
    await setup_halo(hass, sleeping="unavailable")
    m = led_log.mark()
    resp = await indicate(hass, "kitchen", "w", "warn")
    assert resp == {"kitchen": "suppressed_sleep"}
    assert led_log.since(m, KITCHEN_LED) == []


async def test_no_sleep_entity_never_sleeps(hass: HomeAssistant, led_log: LedLog) -> None:
    await setup_halo(hass, make_entry(sleep_entity=None), debounce_s=180, sleeping="on")
    m = led_log.mark()
    await indicate(hass, "master", "w", "warn")
    assert _norm(led_log.since(m, MASTER_LED)) == [WARN]


async def test_boot_debounce_applies_floor_then_lifts(
    hass: HomeAssistant, led_log: LedLog, freezer: FrozenDateTimeFactory
) -> None:
    await setup_halo(hass, debounce_s=180)
    m = led_log.mark()
    resp = await indicate(hass, "kitchen", "w", "warn")
    assert resp == {"kitchen": "suppressed_sleep"}
    resp = await indicate(hass, "kitchen", "c", "critical", ttl_s=60)
    assert resp == {"kitchen": "accepted"}  # critical is never suppressed
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert _norm(led_log.since(m, KITCHEN_LED)) == [CRIT, OFF]
    freezer.tick(timedelta(seconds=120))  # debounce window (180 s) over
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert _norm(led_log.since(m, KITCHEN_LED)) == [CRIT, OFF, WARN]


async def test_boot_debounce_boundary(
    hass: HomeAssistant, led_log: LedLog, freezer: FrozenDateTimeFactory
) -> None:
    await setup_halo(hass, debounce_s=180)
    freezer.tick(timedelta(seconds=179))
    resp = await indicate(hass, "kitchen", "w", "warn")
    assert resp == {"kitchen": "suppressed_sleep"}
    freezer.tick(timedelta(seconds=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    resp = await indicate(hass, "kitchen", "w2", "warn")
    assert resp == {"kitchen": "accepted"}


# --------------------------------------------------------------- availability


async def test_replay_after_unavailable(hass: HomeAssistant, led_log: LedLog) -> None:
    await setup_halo(hass)
    m = led_log.mark()
    await indicate(hass, "kitchen", "w", "warn")
    hass.states.async_set(KITCHEN_LED, "unavailable")
    await hass.async_block_till_done()
    resp = await indicate(hass, "kitchen", "a", "alert")
    assert resp == {"kitchen": "unavailable"}
    assert _norm(led_log.since(m, KITCHEN_LED)) == [WARN]
    assert hass.states.get("sensor.kitchen_halo_status").state == "led_unavailable"
    hass.states.async_set(KITCHEN_LED, "unknown")
    await hass.async_block_till_done()
    assert _norm(led_log.since(m, KITCHEN_LED)) == [WARN, ALERT]
    assert hass.states.get("sensor.kitchen_halo_status").state == "ok"


async def test_replay_resends_same_winner_once(hass: HomeAssistant, led_log: LedLog) -> None:
    await setup_halo(hass)
    m = led_log.mark()
    await indicate(hass, "kitchen", "w", "warn")
    hass.states.async_set(KITCHEN_LED, "unavailable")
    await hass.async_block_till_done()
    hass.states.async_set(KITCHEN_LED, "off")
    await hass.async_block_till_done()
    hass.states.async_set(SLEEP, "off", {"poke": 1})
    await hass.async_block_till_done()
    assert _norm(led_log.since(m, KITCHEN_LED)) == [WARN, WARN]


async def test_release_while_unavailable_restores_on_return(
    hass: HomeAssistant, led_log: LedLog
) -> None:
    await setup_halo(hass)
    m = led_log.mark()
    await indicate(hass, "kitchen", "w", "warn")
    hass.states.async_set(KITCHEN_LED, "unavailable")
    await hass.async_block_till_done()
    await release(hass, "w")
    assert _norm(led_log.since(m, KITCHEN_LED)) == [WARN]
    hass.states.async_set(KITCHEN_LED, "on", {"rgb_color": (0, 0, 255)})
    await hass.async_block_till_done()
    assert _norm(led_log.since(m, KITCHEN_LED)) == [WARN, OFF]


async def test_ks_unavailable_status(hass: HomeAssistant, led_log: LedLog) -> None:
    await setup_halo(hass)
    assert hass.states.get("sensor.kitchen_halo_status").state == "ok"
    hass.states.async_set(KITCHEN_KS, "unavailable")
    await hass.async_block_till_done()
    assert hass.states.get("sensor.kitchen_halo_status").state == "ks_unavailable"


# --------------------------------------------------------------- kill switch


async def test_kill_switch_sends_nothing(hass: HomeAssistant, led_log: LedLog) -> None:
    await setup_halo(hass)
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.halo_led"}, blocking=True
    )
    m = led_log.mark()
    await indicate(hass, "kitchen", "c", "critical")
    await indicate(hass, "master", "w", "warn")
    hass.states.async_set(SLEEP, "on")
    await hass.async_block_till_done()
    await release(hass, "c")
    assert led_log.calls[m:] == []


async def test_kill_switch_off_mid_lease_then_on_reshows(
    hass: HomeAssistant, led_log: LedLog
) -> None:
    await setup_halo(hass)
    m = led_log.mark()
    await indicate(hass, "kitchen", "w", "warn")
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.halo_led"}, blocking=True
    )
    await indicate(hass, "kitchen", "a", "alert")
    assert _norm(led_log.since(m, KITCHEN_LED)) == [WARN, OFF]
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.halo_led"}, blocking=True
    )
    await hass.async_block_till_done()
    assert _norm(led_log.since(m, KITCHEN_LED)) == [WARN, OFF, ALERT]


async def test_kill_switch_restored_off_blocks_stale_restore(
    hass: HomeAssistant, led_log: LedLog, hass_storage
) -> None:
    """switch restored OFF across restart: the stale-snapshot restore must not fire."""
    from pytest_homeassistant_custom_component.common import mock_restore_cache
    from homeassistant.core import State

    mock_restore_cache(hass, [State("switch.halo_led", "off")])
    hass_storage[STORAGE_KEY] = {
        "version": 1,
        "key": STORAGE_KEY,
        "data": {"sub_kitchen": {"state": "off", "rgb_color": None}},
    }
    m = led_log.mark()
    await setup_halo(hass)
    assert led_log.calls[m:] == []
    assert hass.states.get("switch.halo_led").state == "off"


# -------------------------------------------------------------- services / misc


async def test_no_panels_is_inert(hass: HomeAssistant, led_log: LedLog) -> None:
    """No entry loaded: services exist, answer no_surface, send nothing."""
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, DOMAIN, {})
    resp = await indicate(hass, ["kitchen", "media"], "x", "critical")
    assert resp == {"kitchen": "no_surface", "media": "no_surface"}
    assert led_log.calls == []


async def test_unknown_area_no_surface(hass: HomeAssistant, led_log: LedLog) -> None:
    await setup_halo(hass)
    m = led_log.mark()
    resp = await indicate(hass, ["kitchen", "garage"], "x", "warn")
    assert resp == {"kitchen": "accepted", "garage": "no_surface"}
    assert _norm(led_log.since(m, KITCHEN_LED)) == [WARN]


async def test_disabled_panel_no_surface(hass: HomeAssistant, led_log: LedLog) -> None:
    await setup_halo(hass, make_entry(kitchen_enabled=False))
    m = led_log.mark()
    resp = await indicate(hass, "kitchen", "x", "critical")
    assert resp == {"kitchen": "no_surface"}
    assert led_log.calls[m:] == []
    assert hass.states.get("sensor.kitchen_halo_status").state == "disabled"


async def test_release_scoped_by_owner_and_area(hass: HomeAssistant, led_log: LedLog) -> None:
    await setup_halo(hass)
    await indicate(hass, ["kitchen", "master"], "x", "warn", owner="ura")
    await indicate(hass, "kitchen", "y", "alert", owner="ura")
    await indicate(hass, "kitchen", "z", "info", owner="other")
    assert await release(hass, "x", owner="ura", area_id="master") == {"released": 1}
    m = led_log.mark()
    # owner-wide release (no id) leaves the other owner's lease
    assert await release(hass, None, owner="ura") == {"released": 2}
    attrs = hass.states.get("sensor.kitchen_halo_indication").attributes
    assert [a["owner"] for a in attrs["active"]] == ["other"]
    assert _norm(led_log.since(m, MASTER_LED)) == []


async def test_release_all_button(hass: HomeAssistant, led_log: LedLog) -> None:
    await setup_halo(hass)
    await indicate(hass, ["kitchen", "master"], "x", "warn", owner="ura")
    await indicate(hass, "kitchen", "z", "alert", owner="other")
    m = led_log.mark()
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.halo_release_all"}, blocking=True
    )
    await hass.async_block_till_done()
    assert _norm(led_log.since(m, KITCHEN_LED)) == [OFF]
    assert _norm(led_log.since(m, MASTER_LED)) == [OFF]
    assert hass.states.get("sensor.kitchen_halo_indication").state == "idle"


async def test_release_all_service(hass: HomeAssistant, led_log: LedLog) -> None:
    await setup_halo(hass)
    await indicate(hass, "kitchen", "z", "alert", owner="other")
    resp = await hass.services.async_call(
        DOMAIN, "release_all", {}, blocking=True, return_response=True
    )
    assert resp == {"released": 1}


async def test_indication_sensor_attributes(hass: HomeAssistant, led_log: LedLog) -> None:
    await setup_halo(hass)
    await indicate(hass, "kitchen", "w", "warn")
    await indicate(hass, "kitchen", "a", "alert")
    st = hass.states.get("sensor.kitchen_halo_indication")
    assert st.state == "alert"
    assert [a["id"] for a in st.attributes["active"]] == ["a", "w"]
    assert st.attributes["last_cmd"]["effect"] == "pulse"
    assert st.attributes["last_cmd_at"] is not None


def _flaky(hass: HomeAssistant, led_log: LedLog, fail: dict[str, int], exc: Exception):
    """Wrap the fake LED: the first fail[service] calls raise `exc`, then behave."""
    real_on = hass.services._services["light"]["turn_on"].job.target  # noqa: SLF001
    real_off = hass.services._services["light"]["turn_off"].job.target  # noqa: SLF001
    attempts: list[str] = []

    def _make(service, real):
        async def _h(call):
            attempts.append(service)
            if fail.get(service, 0) > 0:
                fail[service] -= 1
                raise exc
            real(call)

        return _h

    hass.services.async_register("light", "turn_on", _make("turn_on", real_on))
    hass.services.async_register("light", "turn_off", _make("turn_off", real_off))
    return attempts


async def test_led_call_failure_is_retried(
    hass: HomeAssistant, led_log: LedLog, freezer: FrozenDateTimeFactory
) -> None:
    """H1: a failed show is retried by its own backoff timer, no outside event needed."""
    from homeassistant.exceptions import HomeAssistantError

    await setup_halo(hass)
    attempts = _flaky(hass, led_log, {"turn_on": 2}, HomeAssistantError("bridge down"))
    m = led_log.mark()
    resp = await indicate(hass, "kitchen", "w", "warn")
    assert resp == {"kitchen": "unavailable"}
    st = hass.states.get("sensor.kitchen_halo_indication")
    assert "bridge down" in st.attributes["last_error"]
    freezer.tick(timedelta(seconds=2))  # first backoff: 2 s -> fails again
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert attempts == ["turn_on", "turn_on"]
    freezer.tick(timedelta(seconds=3))  # second backoff: 4 s, not yet
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert attempts == ["turn_on", "turn_on"]
    freezer.tick(timedelta(seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert attempts == ["turn_on", "turn_on", "turn_on"]
    assert _norm(led_log.since(m, KITCHEN_LED)) == [WARN]
    assert hass.states.get("sensor.kitchen_halo_indication").attributes["last_error"] is None


async def test_failed_restore_is_retried(
    hass: HomeAssistant, led_log: LedLog, freezer: FrozenDateTimeFactory
) -> None:
    from homeassistant.exceptions import HomeAssistantError

    await setup_halo(hass)
    m = led_log.mark()
    await indicate(hass, "kitchen", "w", "warn")
    attempts = _flaky(hass, led_log, {"turn_off": 1}, HomeAssistantError("x"))
    await release(hass, "w")
    assert _norm(led_log.since(m, KITCHEN_LED)) == [WARN]
    freezer.tick(timedelta(seconds=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert attempts == ["turn_off", "turn_off"]
    assert _norm(led_log.since(m, KITCHEN_LED)) == [WARN, OFF]


async def test_retry_cancelled_on_unload(
    hass: HomeAssistant, led_log: LedLog, freezer: FrozenDateTimeFactory
) -> None:
    from homeassistant.exceptions import HomeAssistantError

    entry = await setup_halo(hass)
    hass.states.async_set(KITCHEN_LED, "off")
    attempts = _flaky(hass, led_log, {"turn_on": 99}, HomeAssistantError("x"))
    await indicate(hass, "kitchen", "w", "warn")
    hass.states.async_set(KITCHEN_LED, "unavailable")  # unload cannot restore
    await hass.async_block_till_done()
    kitchen = entry.runtime_data.panels["sub_kitchen"]
    assert kitchen.retry_unsub is not None
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert kitchen.retry_unsub is None
    n = len(attempts)
    freezer.tick(timedelta(seconds=120))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(attempts) == n


async def test_one_shot_consumed_only_after_successful_send(
    hass: HomeAssistant, led_log: LedLog, freezer: FrozenDateTimeFactory
) -> None:
    from homeassistant.exceptions import HomeAssistantError

    await setup_halo(hass)
    _flaky(hass, led_log, {"turn_on": 1}, HomeAssistantError("x"))
    m = led_log.mark()
    await indicate(hass, "kitchen", "i", "info", ttl_s=600)
    freezer.tick(timedelta(seconds=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert _norm(led_log.since(m, KITCHEN_LED))[:1] == [INFO]


async def test_one_panel_failure_does_not_skip_others(
    hass: HomeAssistant, led_log: LedLog
) -> None:
    """L2: a non-HA exception from one LED must not stop the other panel's command."""
    await setup_halo(hass)
    real_on = hass.services._services["light"]["turn_on"].job.target  # noqa: SLF001

    async def _h(call):
        if call.data["entity_id"] == KITCHEN_LED or call.data["entity_id"] == [KITCHEN_LED]:
            raise ValueError("bad payload")
        real_on(call)

    hass.services.async_register("light", "turn_on", _h)
    m = led_log.mark()
    resp = await indicate(hass, ["kitchen", "master"], "w", "warn")
    assert resp == {"kitchen": "unavailable", "master": "accepted"}
    assert _norm(led_log.since(m, MASTER_LED)) == [WARN]


async def test_snapshot_ignores_lagging_echo_of_own_show(hass: HomeAssistant) -> None:
    """H2: HA state lags the bridge echo. A lease arriving right after our restore,
    while the entity still shows our blue, must snapshot the restore target."""
    log: list[tuple[str, dict]] = []
    pending: list[tuple[str, dict]] = []

    async def _h(call):
        data = {k: v for k, v in call.data.items() if k != "entity_id"}
        log.append((call.service, data))
        if call.service == "turn_on":
            pending.append(("on", {"rgb_color": tuple(data["rgb_color"])}))
        else:
            pending.append(("off", {}))

    def _echo():  # deliver the oldest MQTT echo
        state, attrs = pending.pop(0)
        hass.states.async_set(KITCHEN_LED, state, attrs)

    hass.services.async_register("light", "turn_on", _h)
    hass.services.async_register("light", "turn_off", _h)
    await setup_halo(hass)  # kitchen LED off
    await indicate(hass, "kitchen", "w", "warn")
    _echo()  # entity: on, blue
    await release(hass, "w")  # turn_off sent, echo not yet delivered
    await indicate(hass, "kitchen", "a", "alert")
    await release(hass, "a")
    assert _norm(log)[-1] == OFF


async def test_halo_ready_event_on_setup_and_reload(
    hass: HomeAssistant, led_log: LedLog
) -> None:
    """H3: services are not re-registered on reload, so callers re-assert on halo_ready."""
    events = []
    hass.bus.async_listen("halo_ready", events.append)
    entry = await setup_halo(hass)
    assert len(events) == 1
    assert events[0].data == {"areas": ["kitchen", "master"]}
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert len(events) == 2


async def test_kill_switch_off_silences_running_effect(
    hass: HomeAssistant, led_log: LedLog
) -> None:
    """M2: switching the LED off sends one restore per active panel, then nothing."""
    hass.states.async_set(KITCHEN_LED, "on", {"rgb_color": (0, 255, 0)})
    await setup_halo(hass)
    mk = led_log.mark()
    await indicate(hass, "kitchen", "w", "warn")
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.halo_led"}, blocking=True
    )
    await hass.async_block_till_done()
    solid_green = ("turn_on", {"effect": "solid", "rgb_color": [0, 255, 0]})
    assert _norm(led_log.since(mk, KITCHEN_LED)) == [WARN, solid_green]
    assert led_log.since(mk, MASTER_LED) == []  # idle panel: nothing
    await indicate(hass, "kitchen", "c", "critical")
    await release(hass, "w")
    await release(hass, "c")
    assert _norm(led_log.since(mk, KITCHEN_LED)) == [WARN, solid_green]


async def test_one_shot_restored_after_delay(
    hass: HomeAssistant, led_log: LedLog, freezer: FrozenDateTimeFactory
) -> None:
    """M1: after notify plays, restore ~1 s later, well before the TTL."""
    hass.states.async_set(KITCHEN_LED, "on", {"rgb_color": (0, 255, 0)})
    await setup_halo(hass)
    m = led_log.mark()
    await indicate(hass, "kitchen", "i", "info", ttl_s=600)
    assert _norm(led_log.since(m, KITCHEN_LED)) == [INFO]
    freezer.tick(timedelta(seconds=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    solid_green = ("turn_on", {"effect": "solid", "rgb_color": [0, 255, 0]})
    assert _norm(led_log.since(m, KITCHEN_LED)) == [INFO, solid_green]
    assert hass.states.get("sensor.kitchen_halo_indication").state == "info"
    await release(hass, "i")
    assert _norm(led_log.since(m, KITCHEN_LED)) == [INFO, solid_green]


async def test_led_return_resnapshots_when_nothing_shown(
    hass: HomeAssistant, led_log: LedLog
) -> None:
    """M3: lease arrives while the LED is unavailable; on return, snapshot the real state."""
    await setup_halo(hass)
    hass.states.async_set(KITCHEN_LED, "unavailable")
    await hass.async_block_till_done()
    m = led_log.mark()
    await indicate(hass, "kitchen", "w", "warn")
    hass.states.async_set(KITCHEN_LED, "on", {"rgb_color": (0, 255, 0)})
    await hass.async_block_till_done()
    await release(hass, "w")
    assert _norm(led_log.since(m, KITCHEN_LED)) == [
        WARN,
        ("turn_on", {"effect": "solid", "rgb_color": [0, 255, 0]}),
    ]


async def test_expiry_racing_renewal_keeps_new_timer(
    hass: HomeAssistant, led_log: LedLog, freezer: FrozenDateTimeFactory
) -> None:
    """L1: renewal wins the lock before a due expiry callback; the new handle stays tracked."""
    import asyncio

    entry = await setup_halo(hass)
    runtime = entry.runtime_data
    await indicate(hass, "kitchen", "w", "warn", ttl_s=60)
    await runtime._lock.acquire()  # noqa: SLF001
    renew = hass.async_create_task(
        hass.services.async_call(
            "halo",
            "indicate",
            {"area_id": "kitchen", "owner": "t", "id": "w", "level": "warn", "ttl_s": 600},
            blocking=True,
            return_response=True,
        )
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    for _ in range(5):
        await asyncio.sleep(0)
    runtime._lock.release()  # noqa: SLF001
    await renew
    await hass.async_block_till_done()
    assert set(runtime.panels["sub_kitchen"].timers) == {("t", "w")}


async def test_release_cancels_timer_handle(hass: HomeAssistant, led_log: LedLog) -> None:
    """One TTL timer per lease, cancelled on release (R2-13): no handle survives."""
    entry = await setup_halo(hass)
    await indicate(hass, ["kitchen", "master"], "w", "warn", ttl_s=60)
    timers = entry.runtime_data.panels["sub_kitchen"].timers
    assert set(timers) == {("t", "w")}
    await release(hass, "w", area_id="kitchen")
    assert timers == {}
    assert set(entry.runtime_data.panels["sub_master"].timers) == {("t", "w")}


async def test_kill_switch_on_resends_current(hass: HomeAssistant, led_log: LedLog) -> None:
    """Re-enabling the LED re-sends the winner once (the LED may have been changed meanwhile)."""
    await setup_halo(hass)
    m = led_log.mark()
    await indicate(hass, "kitchen", "w", "warn")
    for svc in ("turn_off", "turn_on"):
        await hass.services.async_call(
            "switch", svc, {"entity_id": "switch.halo_led"}, blocking=True
        )
    await hass.async_block_till_done()
    assert _norm(led_log.since(m, KITCHEN_LED)) == [WARN, OFF, WARN]
