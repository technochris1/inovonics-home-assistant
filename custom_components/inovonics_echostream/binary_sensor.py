"""Native binary sensors for EchoStream bits and device connectivity."""

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import callback

from .const import COORDINATOR
from .entity import InovonicsEntity


async def async_setup_entry(hass, entry, async_add_entities):
    runtime = entry.runtime_data
    added = set()

    @callback
    def discover():
        entities = []
        for uid in (COORDINATOR, *runtime.devices):
            key = uid, "connectivity"
            if key not in added:
                added.add(key)
                entities.append(Connectivity(runtime, uid))
        for uid, points in runtime.points.items():
            for suffix, point in points.items():
                if point.get("filtered") and entry.options.get("hide_unsupported", True):
                    continue
                key = uid, suffix
                if key not in added:
                    added.add(key)
                    entities.append(StatusPoint(runtime, uid, suffix, point))
        if entities:
            async_add_entities(entities)

    entry.async_on_unload(runtime.subscribe(discover))
    discover()


class Connectivity(InovonicsEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, runtime, uid):
        super().__init__(runtime, uid, "connectivity", "Connectivity")

    @property
    def is_on(self):
        if self.uid == COORDINATOR:
            return self.runtime.view["connected"]
        return self.runtime.devices.get(self.uid, {}).get("online")


class StatusPoint(InovonicsEntity, BinarySensorEntity):
    def __init__(self, runtime, uid, suffix, point):
        super().__init__(runtime, uid, suffix, point["name"])
        if point["class"]:
            self._attr_device_class = BinarySensorDeviceClass(point["class"])

    @property
    def available(self):
        return self.suffix in self.runtime.points.get(self.uid, {})

    @property
    def is_on(self):
        return self.runtime.points.get(self.uid, {}).get(self.suffix, {}).get("value")
