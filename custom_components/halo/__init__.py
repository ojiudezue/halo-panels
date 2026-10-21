"""Halo: wall-panel presentation surface (LED ring indications with leases)."""

from __future__ import annotations

import probatio as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import (
    ATTR_ANNOUNCE,
    ATTR_CARD,
    ATTR_ID,
    ATTR_LEVEL,
    ATTR_OWNER,
    ATTR_PATTERN,
    ATTR_TTL_S,
    CONF_AREA_ID,
    DEFAULT_TTL_S,
    DOMAIN,
    EFFECTS,
    LEVELS,
    MAX_TTL_S,
    MIN_TTL_S,
    RESULT_NO_SURFACE,
    SERVICE_INDICATE,
    SERVICE_RELEASE,
    SERVICE_RELEASE_ALL,
)
from .runtime import HaloRuntime

PLATFORMS = [Platform.BUTTON, Platform.NUMBER, Platform.SENSOR, Platform.SWITCH]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type HaloConfigEntry = ConfigEntry[HaloRuntime]

INDICATE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_AREA_ID): vol.All(cv.ensure_list, [cv.string]),
        vol.Required(ATTR_OWNER): cv.string,
        vol.Required(ATTR_ID): cv.string,
        vol.Required(ATTR_LEVEL): vol.In(LEVELS),
        vol.Optional(ATTR_PATTERN): vol.In(EFFECTS),
        vol.Optional(ATTR_TTL_S, default=DEFAULT_TTL_S): vol.All(
            vol.Coerce(int), vol.Range(min=MIN_TTL_S, max=MAX_TTL_S)
        ),
        # Accepted now so callers can send them; used from slice S2.
        vol.Optional(ATTR_ANNOUNCE): cv.string,
        vol.Optional(ATTR_CARD): dict,
    }
)

RELEASE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_OWNER): cv.string,
        vol.Optional(ATTR_ID): cv.string,
        vol.Optional(CONF_AREA_ID): vol.All(cv.ensure_list, [cv.string]),
    }
)


def _runtime(hass: HomeAssistant) -> HaloRuntime | None:
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            return entry.runtime_data
    return None


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the services (they answer no_surface when no entry is loaded)."""

    async def _indicate(call: ServiceCall) -> ServiceResponse:
        areas = call.data[CONF_AREA_ID]
        runtime = _runtime(hass)
        if runtime is None:
            return {area: RESULT_NO_SURFACE for area in areas}
        return await runtime.async_indicate(
            areas,
            call.data[ATTR_OWNER],
            call.data[ATTR_ID],
            call.data[ATTR_LEVEL],
            call.data.get(ATTR_PATTERN),
            call.data[ATTR_TTL_S],
        )

    async def _release(call: ServiceCall) -> ServiceResponse:
        runtime = _runtime(hass)
        if runtime is None:
            return {"released": 0}
        n = await runtime.async_release(
            call.data[ATTR_OWNER], call.data.get(ATTR_ID), call.data.get(CONF_AREA_ID)
        )
        return {"released": n}

    async def _release_all(call: ServiceCall) -> ServiceResponse:
        runtime = _runtime(hass)
        if runtime is None:
            return {"released": 0}
        return {"released": await runtime.async_release_all()}

    hass.services.async_register(
        DOMAIN,
        SERVICE_INDICATE,
        _indicate,
        schema=INDICATE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_RELEASE,
        _release,
        schema=RELEASE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_RELEASE_ALL,
        _release_all,
        supports_response=SupportsResponse.OPTIONAL,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: HaloConfigEntry) -> bool:
    runtime = HaloRuntime(hass, entry)
    await runtime.async_load()
    entry.runtime_data = runtime
    # Platforms first: switch.halo_led / number.halo_boot_debounce_s restore their
    # values into the runtime before any LED command can be sent.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await runtime.async_start()
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: HaloConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: HaloConfigEntry) -> bool:
    await entry.runtime_data.async_stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
