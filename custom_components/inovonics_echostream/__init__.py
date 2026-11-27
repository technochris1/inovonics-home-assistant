"""Native EchoStream integration for Home Assistant."""

import voluptuous as vol

from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import ConfigEntryNotReady, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service import async_register_admin_service

from .const import DOMAIN
from .control import MAX_OFFLINE_MINUTES, UID
from .runtime import Runtime

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS = (Platform.BINARY_SENSOR, Platform.SENSOR)
UID_SCHEMA = vol.All(cv.string, vol.Upper, vol.Match(r"^[0-9A-F]{8}$"))
NID_SCHEMA = vol.All(vol.Coerce(int), vol.Range(min=0, max=31))


def active_runtime(hass, entry_id=None):
    entries = hass.config_entries.async_entries(DOMAIN)
    if entry_id is not None:
        entry = next((entry for entry in entries if entry.entry_id == entry_id), None)
    elif len(entries) == 1:
        entry = entries[0]
    else:
        entry = next((entry for entry in entries if entry.unique_id in (None, DOMAIN)), None)
    if entry is None:
        raise ServiceValidationError("Select the Inovonics coordinator for this action.")
    runtime = getattr(entry, "runtime_data", None)
    if runtime is None or not runtime.active:
        raise ServiceValidationError("The selected Inovonics coordinator is unavailable.")
    return runtime


async def async_setup(hass, config):
    async def action(call):
        data = call.data
        runtime = active_runtime(hass, data.get("coordinator"))
        try:
            if call.service == "read_coordinator_nid":
                return {"nid": await runtime.read_coordinator_nid()}
            if call.service == "set_device_offline_timeout":
                await hass.async_add_executor_job(lambda: runtime.control.set_windows(
                    uid=data["uid"], override=data.get("minutes")))
            elif call.service == "set_coordinator_nid":
                if not data["confirm"]:
                    raise ValueError("Confirm the coordinator NID change first.")
                await hass.async_add_executor_job(runtime.control.request, data["nid"])
            else:
                await hass.async_add_executor_job(runtime.control.request, data["nid"], data["uid"])
        except ValueError as error:
            raise ServiceValidationError(str(error)) from error

    coordinator = {vol.Optional("coordinator"): cv.string}
    async_register_admin_service(hass, DOMAIN, "read_coordinator_nid", action, vol.Schema(coordinator),
                                 supports_response=SupportsResponse.ONLY)
    async_register_admin_service(hass, DOMAIN, "set_coordinator_nid", action, vol.Schema({
        **coordinator, vol.Required("nid"): NID_SCHEMA, vol.Required("confirm"): cv.boolean,
    }))
    async_register_admin_service(hass, DOMAIN, "sync_repeater_nid", action, vol.Schema({
        **coordinator, vol.Required("uid"): UID_SCHEMA, vol.Required("nid"): NID_SCHEMA,
    }))
    async_register_admin_service(hass, DOMAIN, "set_device_offline_timeout", action, vol.Schema({
        **coordinator, vol.Required("uid"): UID_SCHEMA,
        vol.Optional("minutes"): vol.All(vol.Coerce(int), vol.Range(min=0, max=MAX_OFFLINE_MINUTES)),
    }))
    return True


async def async_setup_entry(hass, entry):
    try:
        runtime = await Runtime.create(hass, entry)
        entry.runtime_data = runtime
        await runtime.start()
    except (OSError, ValueError) as error:
        if hasattr(entry, "runtime_data"):
            await entry.runtime_data.stop()
        raise ConfigEntryNotReady(str(error)) from error
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        await runtime.stop()
        raise
    async def on_core_stop(_event):
        await runtime.stop()

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, on_core_stop))
    return True


async def async_unload_entry(hass, entry):
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    await entry.runtime_data.stop()
    return True


async def async_remove_config_entry_device(hass, entry, device_entry):
    """Forget one EchoStream sensor or repeater through the device page."""
    runtime = getattr(entry, "runtime_data", None)
    if runtime is None:
        return False
    prefix = runtime.identity_prefix
    for domain, identifier in device_entry.identifiers:
        uid = identifier.removeprefix(prefix)
        if domain == DOMAIN and identifier.startswith(prefix) and UID.fullmatch(uid):
            return await runtime.forget_device(uid)
    return False
