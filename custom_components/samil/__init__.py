"""Samil Power inverter integration (SolarRiver TL-D / SolarLake TL)."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import SamilConfigEntry, SamilCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: SamilConfigEntry) -> bool:
    """Set up Samil from a config entry."""
    coordinator = SamilCoordinator(hass, entry)
    # Never raises ConfigEntryNotReady: if the inverter is off (night) we
    # still set up and entities show "Offline"/last-known values.
    await coordinator.async_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SamilConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded


async def _async_reload(hass: HomeAssistant, entry: SamilConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
