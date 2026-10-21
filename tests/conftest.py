"""Fixtures (PLAN-REVIEW-2 R2-12).

The LED is a fake `light.turn_on` / `light.turn_off` pair writing ONE ordered
command log. Ordering assertions run on that log, never on final state. The
fake mimics the bridge: it publishes {state: on, rgb_color} with no effect
field (MQTTManager.kt:320-335), so a snapshot taken after Halo's own command
would capture Halo's colour (the R2-3 trap).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant, ServiceCall, callback

from custom_components.halo.const import (
    CONF_AREA_ID,
    CONF_BEDROOM,
    CONF_ENABLED,
    CONF_KS_ENTITY,
    CONF_LED_ENTITY,
    CONF_SLEEP_ENTITY,
    DOMAIN,
    SUBENTRY_TYPE_PANEL,
)

SLEEP = "binary_sensor.house_sleeping"
KITCHEN_LED = "light.wallpanel_kitchen_led"
MASTER_LED = "light.wallpanel_master_led"
KITCHEN_KS = "sensor.ks_kitchen_battery"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: Any) -> None:
    """Load custom_components/ in every test."""


@dataclass
class LedLog:
    """Ordered log of (entity_id, service, data) across every LED."""

    calls: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)

    def for_led(self, entity_id: str) -> list[tuple[str, dict[str, Any]]]:
        return [(svc, data) for eid, svc, data in self.calls if eid == entity_id]

    def mark(self) -> int:
        return len(self.calls)

    def since(self, mark: int, entity_id: str) -> list[tuple[str, dict[str, Any]]]:
        return [(svc, data) for eid, svc, data in self.calls[mark:] if eid == entity_id]


@pytest.fixture
def led_log(hass: HomeAssistant) -> LedLog:
    log = LedLog()

    @callback
    def _handle(call: ServiceCall) -> None:
        entity_id = call.data["entity_id"]
        if isinstance(entity_id, list):
            entity_id = entity_id[0]
        data = {k: v for k, v in call.data.items() if k != "entity_id"}
        log.calls.append((entity_id, call.service, data))
        prev = hass.states.get(entity_id)
        if prev is not None and prev.state == "unavailable":
            return
        if call.service == "turn_on":
            attrs = {}
            if "rgb_color" in data:
                attrs["rgb_color"] = tuple(data["rgb_color"])
            elif prev is not None and prev.attributes.get("rgb_color"):
                attrs["rgb_color"] = prev.attributes["rgb_color"]
            hass.states.async_set(entity_id, "on", attrs)
        else:
            hass.states.async_set(entity_id, "off")

    hass.services.async_register("light", "turn_on", _handle)
    hass.services.async_register("light", "turn_off", _handle)
    return log


def make_entry(
    *,
    sleep_entity: str | None = SLEEP,
    kitchen_enabled: bool = True,
    with_ks: bool = True,
) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Halo",
        data={CONF_SLEEP_ENTITY: sleep_entity} if sleep_entity else {},
        subentries_data=[
            {
                "subentry_id": "sub_kitchen",
                "subentry_type": SUBENTRY_TYPE_PANEL,
                "title": "Kitchen",
                "unique_id": "kitchen",
                "data": {
                    CONF_AREA_ID: "kitchen",
                    CONF_LED_ENTITY: KITCHEN_LED,
                    **({CONF_KS_ENTITY: KITCHEN_KS} if with_ks else {}),
                    CONF_BEDROOM: False,
                    CONF_ENABLED: kitchen_enabled,
                },
            },
            {
                "subentry_id": "sub_master",
                "subentry_type": SUBENTRY_TYPE_PANEL,
                "title": "Master",
                "unique_id": "master",
                "data": {
                    CONF_AREA_ID: "master",
                    CONF_LED_ENTITY: MASTER_LED,
                    CONF_BEDROOM: True,
                    CONF_ENABLED: True,
                },
            },
        ],
    )


async def setup_halo(
    hass: HomeAssistant,
    entry: MockConfigEntry | None = None,
    *,
    debounce_s: float = 0,
    sleeping: str = "off",
    kitchen_led: str = "off",
    master_led: str = "off",
) -> MockConfigEntry:
    """Seed states, set up the entry, then (by default) zero the boot debounce."""
    entry = entry or make_entry()
    if hass.states.get(SLEEP) is None:
        hass.states.async_set(SLEEP, sleeping)
    if hass.states.get(KITCHEN_LED) is None:
        hass.states.async_set(KITCHEN_LED, kitchen_led)
    if hass.states.get(MASTER_LED) is None:
        hass.states.async_set(MASTER_LED, master_led)
    if hass.states.get(KITCHEN_KS) is None:
        hass.states.async_set(KITCHEN_KS, "80")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    if debounce_s != 180:
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": "number.halo_boot_debounce_s", "value": debounce_s},
            blocking=True,
        )
        await hass.async_block_till_done()
    return entry


async def indicate(
    hass: HomeAssistant, area: str | list[str], lease_id: str, level: str, **extra: Any
) -> dict[str, Any]:
    resp = await hass.services.async_call(
        DOMAIN,
        "indicate",
        {"area_id": area, "owner": extra.pop("owner", "t"), "id": lease_id, "level": level, **extra},
        blocking=True,
        return_response=True,
    )
    await hass.async_block_till_done()
    return resp


async def release(hass: HomeAssistant, lease_id: str | None, **extra: Any) -> dict[str, Any]:
    data: dict[str, Any] = {"owner": extra.pop("owner", "t"), **extra}
    if lease_id is not None:
        data["id"] = lease_id
    resp = await hass.services.async_call(
        DOMAIN, "release", data, blocking=True, return_response=True
    )
    await hass.async_block_till_done()
    return resp
