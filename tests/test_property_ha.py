"""Randomised end-to-end property run through real HA: services, states, timers.

Same independent oracle as the pure hypothesis test, but every input goes
through the HA surface (halo.indicate / halo.release, sleep entity state,
LED availability, switch.halo_led, frozen time + TTL timers) and every check
reads the LED command log. Seeds are fixed so a failure reproduces.
"""

from __future__ import annotations

from datetime import timedelta
import random

import pytest
from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .conftest import KITCHEN_LED, MASTER_LED, SLEEP, LedLog, indicate, release, setup_halo
from .oracle import Oracle

IDS = ["a", "b", "c"]
LEVELS = ["info", "warn", "alert", "critical"]


def _norm(entry: tuple[str, dict]) -> tuple[str, tuple]:
    svc, data = entry
    if svc == "turn_off":
        return ("restore", ("off", None))
    rgb = tuple(data["rgb_color"]) if data.get("rgb_color") is not None else None
    if data.get("effect") == "solid":
        return ("restore", ("solid", rgb))
    return ("show", (data["effect"], rgb))


@pytest.mark.parametrize("seed", range(40))
async def test_ha_random_sequences_hold_invariants(
    hass: HomeAssistant, led_log: LedLog, freezer: FrozenDateTimeFactory, seed: int
) -> None:
    rnd = random.Random(seed)
    bedroom = seed % 2 == 1
    area, led = ("master", MASTER_LED) if bedroom else ("kitchen", KITCHEN_LED)
    if seed % 3 == 0:
        hass.states.async_set(KITCHEN_LED, "on", {"rgb_color": (0, 255, 0)})
        hass.states.async_set(MASTER_LED, "on", {"rgb_color": (0, 255, 0)})
    await setup_halo(hass)
    oracle = Oracle(bedroom=bedroom)
    sleeping = False
    led_up = True
    kill_on = False
    parked_state = None

    for i in range(30):
        mark = led_log.mark()
        op = rnd.choice(["indicate", "indicate", "release", "tick", "sleep", "led", "kill"])
        where = f"seed {seed} step {i} {op}"
        if op == "indicate":
            lid, level, ttl = rnd.choice(IDS), rnd.choice(LEVELS), rnd.randint(10, 60)
            st = hass.states.get(led)
            vis = (st.state, st.attributes.get("rgb_color"))
            oracle.indicate(lid, level, dt_util.utcnow().timestamp() + ttl, vis)
            await indicate(hass, area, lid, level, ttl_s=ttl)
        elif op == "release":
            lid = rnd.choice(IDS)
            oracle.release(lid)
            await release(hass, lid)
        elif op == "tick":
            freezer.tick(timedelta(seconds=rnd.randint(1, 30)))
            oracle.expire(dt_util.utcnow().timestamp())
            async_fire_time_changed(hass)
            await hass.async_block_till_done()
        elif op == "sleep":
            sleeping = rnd.random() < 0.5
            hass.states.async_set(SLEEP, "on" if sleeping else "off")
            await hass.async_block_till_done()
        elif op == "led":
            if led_up:
                parked_state = hass.states.get(led)
                hass.states.async_set(led, "unavailable")
                led_up = False
            else:
                hass.states.async_set(led, parked_state.state, parked_state.attributes)
                led_up = True
                oracle.output_returned()
            await hass.async_block_till_done()
        else:
            kill_on = not kill_on
            await hass.services.async_call(
                "switch",
                "turn_off" if kill_on else "turn_on",
                {"entity_id": "switch.halo_led"},
                blocking=True,
            )
            await hass.async_block_till_done()
            if not kill_on:
                oracle.output_returned()
        emitted = [_norm(c) for c in led_log.since(mark, led)]
        oracle.check_step(emitted, sleeping, led_up and not kill_on, where)
        assert not oracle.violations, oracle.violations
