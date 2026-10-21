"""Shared entity helpers."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .runtime import HaloRuntime, Panel


def house_device(runtime: HaloRuntime) -> DeviceInfo:
    return DeviceInfo(identifiers={(DOMAIN, runtime.entry.entry_id)}, name="Halo house controls")


def panel_device(panel: Panel) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, panel.key)},
        name=f"{panel.title} panel",
        suggested_area=panel.title,
    )
