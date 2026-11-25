"""Set up and configure the coordinator through Devices & services."""

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv

from .const import DEFAULT_ALERT_SECONDS, DEFAULT_WATCHDOG_SECONDS, DOMAIN, MAX_WATCHDOG_SECONDS
from .control import MAX_OFFLINE_MINUTES, Source
from .debug_relay import DebugRelay

PORT = vol.All(vol.Coerce(int), vol.Range(min=1, max=65535))
MINUTES = vol.All(vol.Coerce(int), vol.Range(min=0, max=MAX_OFFLINE_MINUTES))

SECONDS = vol.All(vol.Coerce(int), vol.Range(min=0, max=MAX_WATCHDOG_SECONDS))


def probe(source):
    with source.open():
        pass


class InovonicsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return InovonicsOptions()

    async def async_step_user(self, user_input=None):
        if not self._async_current_entries():
            return self.async_show_menu(step_id="user", menu_options=["tcp", "serial"])
        return self.async_abort(reason="already_configured")

    async def async_step_reconfigure(self, user_input=None):
        return self.async_show_menu(step_id="reconfigure", menu_options=["tcp", "serial"])

    async def async_step_tcp(self, user_input=None):
        return await self.configure_transport("tcp", user_input)

    async def async_step_serial(self, user_input=None):
        return await self.configure_transport("serial", user_input)

    async def configure_transport(self, mode, user_input):
        existing = self._get_reconfigure_entry() if self.source == "reconfigure" else None
        defaults = existing.data if existing is not None else {}
        errors = {}
        if user_input is not None:
            data = {"connection": mode, **user_input}
            host_key = "processor_host" if mode == "tcp" else "serial_port"
            data[host_key] = data[host_key].strip()
            try:
                source = Source.from_options(data)
                if existing is None or dict(existing.data) != data:
                    await self.hass.async_add_executor_job(probe, source)
            except (OSError, ValueError):
                errors["base"] = "cannot_connect"
            else:
                if existing is not None:
                    return self.async_update_reload_and_abort(existing, title=source.label, data=data)
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=source.label, data=data)

        if mode == "tcp":
            fields = {
                vol.Required("processor_host", default=defaults.get("processor_host", "")): cv.string,
                vol.Required("processor_port", default=defaults.get("processor_port", 10001)): PORT,
            }
        else:
            fields = {
                vol.Required("serial_port", default=defaults.get("serial_port", "")): cv.string,
                vol.Required("serial_baud", default=defaults.get("serial_baud", 9600)):
                    vol.All(vol.Coerce(int), vol.Range(min=300, max=230400)),
            }
        return self.async_show_form(step_id=mode, data_schema=vol.Schema(fields), errors=errors)


class InovonicsOptions(config_entries.OptionsFlowWithReload):
    async def async_step_init(self, user_input=None):
        errors = {}
        if user_input is not None:
            data = dict(user_input)
            if data.get("debug_mode") != "off":
                try:
                    DebugRelay(None, data["debug_mode"], data.get("debug_client"))
                except ValueError:
                    errors["debug_client"] = "invalid_network"
            if not errors:
                return self.async_create_entry(data=data)
        data = self.config_entry.options
        return self.async_show_form(step_id="init", errors=errors, data_schema=vol.Schema({
            vol.Required("device_minutes", default=data.get("device_minutes", 120)): MINUTES,
            vol.Required("repeater_minutes", default=data.get("repeater_minutes", 120)): MINUTES,
            vol.Required("watchdog_seconds", default=data.get("watchdog_seconds", DEFAULT_WATCHDOG_SECONDS)): SECONDS,
            vol.Required("alert_seconds", default=data.get("alert_seconds", DEFAULT_ALERT_SECONDS)): SECONDS,
            vol.Required("hide_unsupported", default=data.get("hide_unsupported", True)): cv.boolean,
            vol.Required("debug_mode", default=data.get("debug_mode", "off")):
                vol.In(("off", "receive", "passthrough", "trace")),
            vol.Optional("debug_client", default=data.get("debug_client", "")): cv.string,
            vol.Required("debug_port", default=data.get("debug_port", 19001)): PORT,
        }))
