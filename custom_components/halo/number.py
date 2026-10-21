"""number.halo_boot_debounce_s: after setup, apply the sleep floor for this long."""

from __future__ import annotations

from homeassistant.components.number import NumberMode, RestoreNumber
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HaloConfigEntry
from .const import DEFAULT_BOOT_DEBOUNCE_S, MAX_BOOT_DEBOUNCE_S
from .entity import house_device


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HaloConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([BootDebounceNumber(entry)])


class BootDebounceNumber(RestoreNumber):
    """0 disables the window. During it, sleep is treated as unknown -> floor applies."""

    _attr_has_entity_name = True
    _attr_translation_key = "boot_debounce_s"
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = 0
    _attr_native_max_value = MAX_BOOT_DEBOUNCE_S
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS

    def __init__(self, entry: HaloConfigEntry) -> None:
        self._runtime = entry.runtime_data
        self._attr_unique_id = f"{entry.entry_id}_boot_debounce_s"
        self.entity_id = "number.halo_boot_debounce_s"  # stable id, independent of device name
        self._attr_device_info = house_device(self._runtime)

    async def async_added_to_hass(self) -> None:
        last = await self.async_get_last_number_data()
        value = (
            last.native_value
            if last is not None and last.native_value is not None
            else DEFAULT_BOOT_DEBOUNCE_S
        )
        await self._runtime.async_set_boot_debounce(value)

    @property
    def native_value(self) -> float:
        return self._runtime.boot_debounce_s

    async def async_set_native_value(self, value: float) -> None:
        await self._runtime.async_set_boot_debounce(value)
        self.async_write_ha_state()
