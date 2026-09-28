"""Data update coordinator for the Samil Power integration."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_INTERFACE,
    CONF_INVERTER_HOST,
    CONF_SCAN_INTERVAL,
    DATA_MODEL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    OFFLINE_RETRY_INTERVAL,
)
from .protocol import (
    Inverter,
    InverterConnectionError,
    InverterFinder,
    InverterNotFoundError,
)

_LOGGER = logging.getLogger(__name__)

type SamilConfigEntry = ConfigEntry[SamilCoordinator]


@dataclass
class SamilData:
    """Snapshot handed to entities."""

    online: bool
    status: dict[str, Any] = field(default_factory=dict)  # last known values
    model: dict[str, str] = field(default_factory=dict)


class SamilClient:
    """Blocking client that keeps one inverter connection alive."""

    def __init__(self, interface_ip: str, inverter_host: str) -> None:
        self._finder = InverterFinder(interface_ip, inverter_host)
        self._inverter: Inverter | None = None
        self._lock = threading.Lock()
        self._next_search = 0.0
        self.model: dict[str, str] = {}

    def _connect(self, attempts: int) -> None:
        self._inverter = self._finder.find(attempts=attempts)
        try:
            self.model = self._inverter.model()
        except InverterConnectionError:
            self._drop()
            raise

    def _drop(self) -> None:
        if self._inverter is not None:
            self._inverter.close()
            self._inverter = None

    def fetch(self) -> dict[str, Any] | None:
        """Return fresh status, or None if the inverter is unreachable."""
        with self._lock:
            for _ in range(2):  # second pass = reconnect after a dropped link
                if self._inverter is None:
                    if time.monotonic() < self._next_search:
                        return None
                    try:
                        self._connect(attempts=2)
                    except (InverterNotFoundError, InverterConnectionError) as err:
                        _LOGGER.debug("Inverter not reachable: %s", err)
                        self._next_search = time.monotonic() + OFFLINE_RETRY_INTERVAL
                        return None
                try:
                    return self._inverter.status()  # type: ignore[union-attr]
                except InverterConnectionError as err:
                    _LOGGER.debug("Lost inverter connection: %s", err)
                    self._drop()
            return None

    def connect_once(self, attempts: int = 4) -> dict[str, str]:
        """Used by the config flow: connect, read model info, disconnect."""
        with self._lock:
            try:
                self._connect(attempts=attempts)
                return dict(self.model)
            finally:
                self.close_unlocked()

    def close_unlocked(self) -> None:
        self._drop()
        self._finder.close()

    def close(self) -> None:
        with self._lock:
            self.close_unlocked()


class SamilCoordinator(DataUpdateCoordinator[SamilData]):
    """Polls the inverter."""

    config_entry: SamilConfigEntry

    def __init__(self, hass: HomeAssistant, entry: SamilConfigEntry) -> None:
        interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=interval),
            always_update=False,
        )
        self.client = SamilClient(
            entry.data.get(CONF_INTERFACE, ""), entry.data.get(CONF_INVERTER_HOST, "")
        )
        self._last_status: dict[str, Any] = {}
        self._was_online: bool | None = None

    async def _async_update_data(self) -> SamilData:
        status = await self.hass.async_add_executor_job(self.client.fetch)
        online = status is not None
        if online:
            self._last_status = status
        if online != self._was_online:
            _LOGGER.info("Samil inverter is %s", "online" if online else "offline")
            self._was_online = online
        model = self.client.model or self.config_entry.data.get(DATA_MODEL, {})
        return SamilData(online=online, status=dict(self._last_status), model=model)

    async def async_shutdown(self) -> None:
        await super().async_shutdown()
        await self.hass.async_add_executor_job(self.client.close)
