"""button.halo_release_all: operator override, clears every owner's leases."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HaloConfigEntry
from .entity import house_device


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HaloConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([ReleaseAllButton(entry)])


class ReleaseAllButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "release_all"

    def __init__(self, entry: HaloConfigEntry) -> None:
        self._runtime = entry.runtime_data
        self._attr_unique_id = f"{entry.entry_id}_release_all"
        self.entity_id = "button.halo_release_all"  # stable id, independent of device name
        self._attr_device_info = house_device(self._runtime)

    async def async_press(self) -> None:
        await self._runtime.async_release_all()
