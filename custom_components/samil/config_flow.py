"""Config flow for the Samil Power integration."""

from __future__ import annotations

import errno
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback

from .const import (
    CONF_INTERFACE,
    CONF_INVERTER_HOST,
    CONF_SCAN_INTERVAL,
    DATA_MODEL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .coordinator import SamilClient, SamilConfigEntry
from .protocol import InverterConnectionError, InverterNotFoundError

_LOGGER = logging.getLogger(__name__)


class SamilConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Samil Power."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        # Only one entry: every instance would need TCP port 1200.
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        errors: dict[str, str] = {}
        if user_input is not None:
            data = {
                CONF_INVERTER_HOST: user_input.get(CONF_INVERTER_HOST, "").strip(),
                CONF_INTERFACE: user_input.get(CONF_INTERFACE, "").strip(),
            }
            client = SamilClient(data[CONF_INTERFACE], data[CONF_INVERTER_HOST])
            try:
                model = await self.hass.async_add_executor_job(client.connect_once)
            except InverterNotFoundError:
                errors["base"] = "not_found"
            except InverterConnectionError:
                errors["base"] = "cannot_connect"
            except OSError as err:
                if err.errno == errno.EADDRINUSE:
                    errors["base"] = "port_in_use"
                else:
                    _LOGGER.exception("Unexpected socket error")
                    errors["base"] = "cannot_connect"
            else:
                serial = model.get("serial_number") or "unknown"
                await self.async_set_unique_id(serial)
                self._abort_if_unique_id_configured()
                name = model.get("model_name") or "Samil inverter"
                return self.async_create_entry(
                    title=f"{name} ({serial})",
                    data={**data, DATA_MODEL: model},
                )

        schema = vol.Schema(
            {
                vol.Optional(CONF_INVERTER_HOST, default=""): str,
                vol.Optional(CONF_INTERFACE, default=""): str,
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or {}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: SamilConfigEntry) -> OptionsFlow:
        return SamilOptionsFlow()


class SamilOptionsFlow(OptionsFlow):
    """Options: polling interval."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        current = self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SCAN_INTERVAL, default=current): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
                    )
                }
            ),
        )
