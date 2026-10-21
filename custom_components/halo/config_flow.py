"""Config flow: one entry per house, one `panel` subentry per wall panel."""

from __future__ import annotations

from typing import Any

import probatio as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import selector

from .const import (
    CONF_AREA_ID,
    CONF_BEDROOM,
    CONF_ENABLED,
    CONF_KS_ENTITY,
    CONF_LED_ENTITY,
    CONF_SLEEP_ENTITY,
    DOMAIN,
    SUBENTRY_TYPE_PANEL,
    SUGGESTED_SLEEP_ENTITY,
)

_SLEEP_SELECTOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain=["binary_sensor", "input_boolean"])
)


def _sleep_schema(suggested: str | None) -> vol.Schema:
    # suggested_value, not default: the field can be cleared (no sleep rule).
    return vol.Schema(
        {
            vol.Optional(
                CONF_SLEEP_ENTITY, description={"suggested_value": suggested}
            ): _SLEEP_SELECTOR
        }
    )


class HaloConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_TYPE_PANEL: PanelSubentryFlow}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> HaloOptionsFlow:
        return HaloOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="Halo", data=user_input)
        default = (
            SUGGESTED_SLEEP_ENTITY
            if self.hass.states.get(SUGGESTED_SLEEP_ENTITY) is not None
            else None
        )
        return self.async_show_form(step_id="user", data_schema=_sleep_schema(default))


class HaloOptionsFlow(OptionsFlow):
    """Change the sleep entity (stored in entry data)."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={**self.config_entry.data, CONF_SLEEP_ENTITY: user_input.get(CONF_SLEEP_ENTITY)},
            )
            return self.async_create_entry(data={})
        return self.async_show_form(
            step_id="init",
            data_schema=_sleep_schema(self.config_entry.data.get(CONF_SLEEP_ENTITY)),
        )


def _panel_schema(defaults: dict[str, Any]) -> vol.Schema:
    ks = defaults.get(CONF_KS_ENTITY)
    return vol.Schema(
        {
            vol.Required(CONF_AREA_ID, default=defaults.get(CONF_AREA_ID, vol.UNDEFINED)): (
                selector.AreaSelector()
            ),
            vol.Required(
                CONF_LED_ENTITY, default=defaults.get(CONF_LED_ENTITY, vol.UNDEFINED)
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="light")),
            vol.Optional(
                CONF_KS_ENTITY, description={"suggested_value": ks}
            ): selector.EntitySelector(),
            vol.Required(CONF_BEDROOM, default=defaults.get(CONF_BEDROOM, False)): bool,
            vol.Required(CONF_ENABLED, default=defaults.get(CONF_ENABLED, True)): bool,
        }
    )


class PanelSubentryFlow(ConfigSubentryFlow):
    """Add or reconfigure one panel: area, LED light, KS liveness entity, flags."""

    def _title(self, area_id: str) -> str:
        area = ar.async_get(self.hass).async_get_area(area_id)
        return area.name if area else area_id

    def _area_taken(self, area_id: str, exclude: str | None = None) -> bool:
        return any(
            sub.data.get(CONF_AREA_ID) == area_id and sub.subentry_id != exclude
            for sub in self._get_entry().subentries.values()
            if sub.subentry_type == SUBENTRY_TYPE_PANEL
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if self._area_taken(user_input[CONF_AREA_ID]):
                errors[CONF_AREA_ID] = "area_taken"
            else:
                return self.async_create_entry(
                    title=self._title(user_input[CONF_AREA_ID]),
                    data=user_input,
                    unique_id=user_input[CONF_AREA_ID],
                )
        return self.async_show_form(
            step_id="user", data_schema=_panel_schema(user_input or {}), errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        sub = self._get_reconfigure_subentry()
        errors: dict[str, str] = {}
        if user_input is not None:
            if self._area_taken(user_input[CONF_AREA_ID], exclude=sub.subentry_id):
                errors[CONF_AREA_ID] = "area_taken"
            else:
                return self.async_update_and_abort(
                    self._get_entry(),
                    sub,
                    title=self._title(user_input[CONF_AREA_ID]),
                    data=user_input,
                    unique_id=user_input[CONF_AREA_ID],
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_panel_schema(user_input or dict(sub.data)),
            errors=errors,
        )
