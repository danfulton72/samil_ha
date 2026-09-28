"""End-to-end tests against a fake inverter over real localhost sockets."""

import socket
from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.samil.const import DOMAIN

from .fake_inverter import FakeInverter

pytestmark = pytest.mark.enable_socket


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations, socket_enabled):
    yield


@pytest.fixture
def inverter():
    inv = FakeInverter()
    yield inv
    inv.close()


async def _setup(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"inverter_host": "127.0.0.1", "interface": ""}
    )
    return result


async def test_full_lifecycle(hass, inverter):
    result = await _setup(hass)
    assert result["type"] is FlowResultType.CREATE_ENTRY, result
    assert result["title"] == "SolarRiver 4500T (DD2142L102)"
    await hass.async_block_till_done()

    s = lambda n: hass.states.get(f"sensor.samil_solarriver_4500t_{n}")
    assert s("output_power").state == "2589.0"
    assert s("energy_total").state == "7701.2"
    assert s("energy_today").state == "21.2"
    assert s("grid_voltage").state == "242.6"
    assert s("grid_frequency").state == "50.01"
    assert s("internal_temperature").state == "35.1"
    assert s("operation_mode").state == "Normal"
    assert s("heatsink_temperature").state == STATE_UNAVAILABLE  # not in format

    # Second poll reuses the connection.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert s("output_power").state == "2589.0"

    # Sunset
    inverter.go_offline()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=22))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert s("operation_mode").state == "Offline"
    assert s("output_power").state == "0"
    assert s("energy_total").state == "7701.2"
    assert s("grid_voltage").state == STATE_UNAVAILABLE

    # Sunrise: after the offline back-off the inverter is found again.
    inverter.online = True
    with patch("custom_components.samil.coordinator.time.monotonic", return_value=1e12):
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=60))
        await hass.async_block_till_done(wait_background_tasks=True)
    assert s("operation_mode").state == "Normal"

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.unique_id == "DD2142L102"
    assert await hass.config_entries.async_unload(entry.entry_id)

    # Port 1200 must have been released on unload.
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", 1200))
    sock.close()


async def test_port_in_use(hass, inverter):
    blocker = socket.socket()
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind(("", 1200))
    blocker.listen()
    try:
        result = await _setup(hass)
    finally:
        blocker.close()
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "port_in_use"}


async def test_not_found(hass, inverter):
    inverter.online = False
    with patch("custom_components.samil.protocol.time.sleep"):
        result = await _setup(hass)
    assert result["errors"] == {"base": "not_found"}


async def test_options_flow(hass, inverter):
    await _setup(hass)
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"scan_interval": 30})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.runtime_data.update_interval == timedelta(seconds=30)
    assert await hass.config_entries.async_unload(entry.entry_id)
