"""Config flow + panel subentry flow."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import area_registry as ar

from custom_components.halo.const import DOMAIN

from .conftest import KITCHEN_LED, LedLog, make_entry, setup_halo


async def test_user_step_creates_entry_with_sleep_entity(hass: HomeAssistant) -> None:
    hass.states.async_set("binary_sensor.ura_presence_coordinator_house_sleeping", "off")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"sleep_entity": "binary_sensor.ura_presence_coordinator_house_sleeping"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        "sleep_entity": "binary_sensor.ura_presence_coordinator_house_sleeping"
    }


async def test_single_instance(hass: HomeAssistant, led_log: LedLog) -> None:
    await setup_halo(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_add_panel_subentry_and_duplicate_area(
    hass: HomeAssistant, led_log: LedLog
) -> None:
    area = ar.async_get(hass).async_create("Media Room")
    entry = await setup_halo(hass)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "panel"), context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    data = {
        "area_id": area.id,
        "led_entity": "light.wallpanel_media_room_led",
        "bedroom": False,
        "enabled": True,
    }
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], data)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Media Room"
    await hass.async_block_till_done()
    # reload picked up the new panel
    hass.states.async_set("light.wallpanel_media_room_led", "off")
    resp = await hass.services.async_call(
        DOMAIN,
        "indicate",
        {"area_id": area.id, "owner": "t", "id": "x", "level": "warn"},
        blocking=True,
        return_response=True,
    )
    assert resp == {area.id: "accepted"}

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "panel"), context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], data)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"area_id": "area_taken"}


async def test_reconfigure_panel(hass: HomeAssistant, led_log: LedLog) -> None:
    entry = await setup_halo(hass)
    result = await entry.start_subentry_reconfigure_flow(hass, "sub_kitchen")
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"area_id": "kitchen", "led_entity": KITCHEN_LED, "bedroom": True, "enabled": True},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert entry.subentries["sub_kitchen"].data["bedroom"] is True


async def test_options_change_sleep_entity(hass: HomeAssistant, led_log: LedLog) -> None:
    entry = await setup_halo(hass, make_entry())
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"sleep_entity": "input_boolean.sleep"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.data["sleep_entity"] == "input_boolean.sleep"
    assert entry.runtime_data.sleep_entity == "input_boolean.sleep"
