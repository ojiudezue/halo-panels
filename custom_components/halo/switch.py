"""switch.halo_led: master kill. Off = no LED command is ever sent."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_OFF
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import HaloConfigEntry
from .entity import house_device


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HaloConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([HaloLedSwitch(entry)])


class HaloLedSwitch(SwitchEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "led"
    _attr_should_poll = False

    def __init__(self, entry: HaloConfigEntry) -> None:
        self._runtime = entry.runtime_data
        self._attr_unique_id = f"{entry.entry_id}_led"
        self._attr_device_info = house_device(self._runtime)

    async def async_added_to_hass(self) -> None:
        last = await self.async_get_last_state()
        if last is not None and last.state == STATE_OFF:
            await self._runtime.async_set_led_enabled(False)

    @property
    def is_on(self) -> bool:
        return self._runtime.led_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._runtime.async_set_led_enabled(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._runtime.async_set_led_enabled(False)
        self.async_write_ha_state()
