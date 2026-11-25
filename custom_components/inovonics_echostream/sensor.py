"""Native diagnostic sensors for NIDs, last heard time, and operations."""

from datetime import datetime, timezone

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import callback

from .const import COORDINATOR
from .entity import InovonicsEntity


async def async_setup_entry(hass, entry, async_add_entities):
    runtime = entry.runtime_data
    async_add_entities([
        Diagnostic(runtime, COORDINATOR, "last_rx", "Last coordinator data"),
        Diagnostic(runtime, COORDINATOR, "nid", "Network ID"),
        Diagnostic(runtime, COORDINATOR, "notice", "NID operation"),
    ])
    added = set()

    @callback
    def discover():
        entities = []
        for uid, device in runtime.devices.items():
            if uid in added:
                continue
            added.add(uid)
            entities.append(Diagnostic(runtime, uid, "seen", "Last heard"))
            if device["kind"] == "repeater":
                entities.append(Diagnostic(runtime, uid, "nid", "Reported network ID"))
        if entities:
            async_add_entities(entities)

    entry.async_on_unload(runtime.subscribe(discover))
    discover()


class Diagnostic(InovonicsEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, runtime, uid, suffix, name):
        super().__init__(runtime, uid, suffix, name)
        if suffix in ("seen", "last_rx"):
            self._attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self):
        if self.uid == COORDINATOR:
            value = self.runtime.view.get(self.suffix)
        else:
            value = self.runtime.devices.get(self.uid, {}).get(self.suffix)
        if value is not None and self.suffix in ("seen", "last_rx"):
            return datetime.fromtimestamp(value, timezone.utc)
        return value
