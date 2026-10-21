"""Per-panel indication and status sensors."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import HaloConfigEntry
from .const import (
    INDICATION_IDLE,
    LEVELS,
    SIGNAL_PANEL_UPDATED,
    STATUS_DISABLED,
    STATUS_KS_UNAVAILABLE,
    STATUS_LED_UNAVAILABLE,
    STATUS_OK,
)
from .entity import panel_device
from .runtime import HaloRuntime, Panel


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HaloConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    for panel in runtime.panels.values():
        async_add_entities(
            [IndicationSensor(runtime, panel), StatusSensor(runtime, panel)],
            config_subentry_id=panel.key,
        )


class _PanelSensor(SensorEntity):
    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(self, runtime: HaloRuntime, panel: Panel, suffix: str) -> None:
        self._runtime = runtime
        self._panel = panel
        self._attr_unique_id = f"{panel.key}_{suffix}"
        self._attr_translation_key = suffix
        self._attr_device_info = panel_device(panel)
        self.entity_id = f"sensor.halo_{panel.area_id}_{suffix}"

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_PANEL_UPDATED.format(self._panel.key),
                self._refresh,
            )
        )

    @callback
    def _refresh(self) -> None:
        self.async_write_ha_state()


class IndicationSensor(_PanelSensor):
    _attr_options = [INDICATION_IDLE, *LEVELS]

    def __init__(self, runtime: HaloRuntime, panel: Panel) -> None:
        super().__init__(runtime, panel, "indication")

    @property
    def native_value(self) -> str:
        return self._runtime.indication(self._panel)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        panel = self._panel
        floor = self._runtime._floor(panel)  # noqa: SLF001
        active = [
            {
                "owner": lease.owner,
                "id": lease.id,
                "level": lease.level,
                "effect": lease.effect,
                "expires_at": dt_util.utc_from_timestamp(lease.expires_at).isoformat(),
                "suppressed": floor is not None and lease.rank < floor,
            }
            for lease in sorted(
                panel.arbiter.leases.values(), key=lambda lease: (-lease.rank, -lease.seq)
            )
        ]
        cmd = panel.last_cmd
        return {
            "active": active,
            "last_cmd": None
            if cmd is None
            else {"kind": cmd.kind, "effect": cmd.effect, "rgb": cmd.rgb, "level": cmd.level},
            "last_cmd_at": panel.last_cmd_at.isoformat() if panel.last_cmd_at else None,
            "last_error": panel.last_error,
            "sleeping": self._runtime.sleeping(),
        }


class StatusSensor(_PanelSensor):
    _attr_options = [STATUS_OK, STATUS_LED_UNAVAILABLE, STATUS_KS_UNAVAILABLE, STATUS_DISABLED]

    def __init__(self, runtime: HaloRuntime, panel: Panel) -> None:
        super().__init__(runtime, panel, "status")

    @property
    def native_value(self) -> str:
        return self._runtime.status(self._panel)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "area_id": self._panel.area_id,
            "led_entity": self._panel.led_entity,
            "ks_entity": self._panel.ks_entity,
            "bedroom": self._panel.bedroom,
        }
