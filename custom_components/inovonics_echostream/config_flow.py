"""Set up and configure the coordinator through Devices & services."""

from asyncio import sleep

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.selector import SelectOptionDict, SelectSelector, SelectSelectorConfig

from .const import DEFAULT_ALERT_SECONDS, DEFAULT_WATCHDOG_SECONDS, DOMAIN, MAX_WATCHDOG_SECONDS
from .control import MAX_OFFLINE_MINUTES, Source, read_coordinator_serial
from .debug_relay import DebugRelay

PORT = vol.All(vol.Coerce(int), vol.Range(min=1, max=65535))
MINUTES = vol.All(vol.Coerce(int), vol.Range(min=0, max=MAX_OFFLINE_MINUTES))
NID = vol.All(vol.Coerce(int), vol.Range(min=0, max=31))

SECONDS = vol.All(vol.Coerce(int), vol.Range(min=0, max=MAX_WATCHDOG_SECONDS))


def probe(source):
    with source.open() as connection:
        return read_coordinator_serial(source, connection)


class InovonicsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return InovonicsOptions()

    async def async_step_user(self, user_input=None):
        return self.async_show_menu(step_id="user", menu_options=["tcp", "serial"])

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
                result = await self.save_transport(source, data, existing)
            except (OSError, ValueError):
                errors["base"] = "cannot_connect"
            else:
                return result

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

    async def save_transport(self, source, data, existing):
        other_entries = [entry for entry in self._async_current_entries() if entry is not existing]
        if any(Source.from_options(entry.data).connection_key == source.connection_key for entry in other_entries):
            return self.async_abort(reason="connection_in_use")

        previous_serial = existing.data.get("coordinator_serial") if existing else None
        if existing and Source.from_options(existing.data).connection_key == source.connection_key:
            if previous_serial is not None:
                data["coordinator_serial"] = previous_serial
            return self.async_update_reload_and_abort(existing, title=source.label, data=data)

        serial = await self.hass.async_add_executor_job(probe, source)
        if previous_serial is not None and previous_serial != serial:
            return self.async_abort(reason="different_coordinator")
        if any(entry.data.get("coordinator_serial") == serial for entry in other_entries):
            return self.async_abort(reason="already_configured")
        if any(entry.data.get("coordinator_serial") is None for entry in other_entries):
            return self.async_abort(reason="unknown_existing_coordinator")

        data["coordinator_serial"] = serial
        if existing is not None:
            return self.async_update_reload_and_abort(existing, title=source.label, data=data)
        await self.async_set_unique_id(f"coordinator-{serial}")
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=source.label, data=data)


class InovonicsOptions(config_entries.OptionsFlowWithReload):
    def __init__(self):
        self._control = None
        self._change = None
        self._repeater_uid = None
        self._progress = None
        self._closed = False
        self._current_nid = None

    async def async_step_init(self, user_input=None):
        return self.async_show_menu(step_id="init", menu_options=["settings", "coordinator_nid", "repeater"])

    async def async_step_coordinator_nid(self, user_input=None):
        return self.async_show_menu(step_id="coordinator_nid", menu_options=["read_nid", "set_nid"])

    async def async_step_read_nid(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=dict(self.config_entry.options))
        runtime = getattr(self.config_entry, "runtime_data", None)
        if runtime is None or not runtime.active:
            return self.async_abort(reason="coordinator_unavailable")
        try:
            nid = await runtime.read_coordinator_nid()
        except ValueError as error:
            return self.async_abort(reason="nid_operation_unavailable", description_placeholders={"error": str(error)})
        return self.async_show_form(
            step_id="read_nid", data_schema=vol.Schema({}), description_placeholders={"nid": str(nid)}, last_step=True,
        )

    async def async_step_set_nid(self, user_input=None):
        runtime = getattr(self.config_entry, "runtime_data", None)
        if runtime is None or not runtime.active:
            return self.async_abort(reason="coordinator_unavailable")
        errors = {}
        try:
            if self._current_nid is None:
                self._current_nid = await runtime.read_coordinator_nid()
            if user_input is not None and user_input.get("confirm"):
                self._control = runtime.control
                self._change = await self.hass.async_add_executor_job(
                    lambda: self._control.request(user_input["nid"], expected_nid=self._current_nid),
                )
                if self._closed:
                    await self.hass.async_add_executor_job(self._control.cancel_change, self._change)
                    return self.async_abort(reason="coordinator_nid_unverified")
                return await self.async_step_verify_nid()
            if user_input is not None:
                errors["confirm"] = "confirm_nid"
        except ValueError as error:
            return self.async_abort(reason="nid_operation_unavailable", description_placeholders={"error": str(error)})
        return self.async_show_form(
            step_id="set_nid", errors=errors, description_placeholders={"nid": str(self._current_nid)},
            data_schema=vol.Schema({
                vol.Required("nid", default=(user_input or {}).get("nid", self._current_nid)): NID,
                vol.Required("confirm", default=False): bool,
            }),
        )

    async def async_step_verify_nid(self, user_input=None):
        return self.change_progress("verify_nid", "coordinator_nid_result", {"nid": str(self._change.target)})

    async def async_step_coordinator_nid_result(self, user_input=None):
        _, result = await self.hass.async_add_executor_job(self._control.change_status, self._change)
        if result not in ("confirmed", "already_matched"):
            return self.async_abort(reason="coordinator_nid_unverified")
        return self.async_show_form(
            step_id="coordinator_nid_complete", data_schema=vol.Schema({}), last_step=True,
            description_placeholders={"nid": str(self._change.target)},
        )

    async def async_step_coordinator_nid_complete(self, user_input=None):
        return self.async_create_entry(title="", data=dict(self.config_entry.options))

    async def async_step_settings(self, user_input=None):
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
        return self.async_show_form(step_id="settings", errors=errors, data_schema=vol.Schema({
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

    async def async_step_repeater(self, user_input=None):
        runtime = getattr(self.config_entry, "runtime_data", None)
        if runtime is None or not runtime.active or not runtime.view["connected"]:
            return self.async_abort(reason="coordinator_unavailable")
        repeaters = {
            uid: device for uid, device in runtime.devices.items() if device["kind"] == "repeater"
        }
        if not repeaters:
            return self.async_abort(reason="no_repeaters")
        if user_input is not None:
            self._control = runtime.control
            self._repeater_uid = user_input["uid"]
            return await self.async_step_prepare_repeater()
        options = [SelectOptionDict(value=uid, label=f"{device['model']} ({uid})") for uid, device in repeaters.items()]
        return self.async_show_form(step_id="repeater", data_schema=vol.Schema({
            vol.Required("uid"): SelectSelector(SelectSelectorConfig(options=options)),
        }))

    async def async_step_prepare_repeater(self, user_input=None):
        if user_input is None:
            return self.async_show_form(
                step_id="prepare_repeater", data_schema=vol.Schema({}),
                description_placeholders={"uid": self._repeater_uid},
            )
        try:
            self._change = await self.hass.async_add_executor_job(
                self._control.start_repeater_setup, self._repeater_uid,
            )
        except ValueError as error:
            return self.async_abort(reason="repeater_unavailable", description_placeholders={"error": str(error)})
        if self._closed:
            await self.hass.async_add_executor_job(self._control.cancel_change, self._change)
            return self.async_abort(reason="repeater_failed")
        return await self.async_step_first_reset()

    def repeater_progress(self, step, ready_stages):
        return self.change_progress(
            step, "repeater_result", {"uid": self._repeater_uid, "nid": str(self._change.target)}, ready_stages,
        )

    def change_progress(self, step, next_step, placeholders, ready_stages=()):
        if self._progress is not None and self._progress.done():
            self._progress.result()
            self._progress = None
            return self.async_show_progress_done(next_step_id=next_step)
        if self._progress is None:
            self._progress = self.hass.async_create_task(self.wait_for_change(ready_stages))
        return self.async_show_progress(
            step_id=step, progress_action=step, progress_task=self._progress,
            description_placeholders=placeholders,
        )

    async def wait_for_change(self, ready_stages):
        while True:
            stage, result = await self.hass.async_add_executor_job(self._control.change_status, self._change)
            if result is not None or stage in ready_stages:
                return
            await sleep(0.2)

    async def async_step_first_reset(self, user_input=None):
        return self.repeater_progress("first_reset", ("sent", "accepted"))

    async def async_step_second_reset(self, user_input=None):
        return self.repeater_progress("second_reset", ())

    async def async_step_repeater_result(self, user_input=None):
        _, result = await self.hass.async_add_executor_job(self._control.change_status, self._change)
        if result is None:
            return await self.async_step_second_reset()
        if result not in ("confirmed", "already_matched"):
            return self.async_abort(reason=result)
        return await self.async_step_repeater_complete(user_input)

    async def async_step_repeater_complete(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=dict(self.config_entry.options))
        return self.async_show_form(
            step_id="repeater_complete", data_schema=vol.Schema({}),
            description_placeholders={"uid": self._repeater_uid, "nid": str(self._change.target)},
            last_step=True,
        )

    @callback
    def async_remove(self):
        self._closed = True
        if self._progress is not None:
            self._progress.cancel()
        if self._change is not None:
            self.hass.async_add_executor_job(self._control.cancel_change, self._change)
