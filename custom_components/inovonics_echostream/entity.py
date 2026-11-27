"""Shared device identity and push updates for EchoStream entities."""

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import COORDINATOR, DOMAIN


class InovonicsEntity(Entity):
    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, runtime, uid, suffix, name):
        self.runtime = runtime
        self.uid = uid
        self.suffix = suffix
        self._identifier = f"{runtime.identity_prefix}{uid}"
        self._attr_unique_id = f"{self._identifier}.{suffix}"
        self._attr_name = name
        self.last_state = None

    @property
    def device_info(self):
        if self.uid == COORDINATOR:
            name = "Inovonics Coordinator"
            if self.runtime.identity_prefix:
                name = f"{name} {self.runtime.entry.data['coordinator_serial']}"
            return DeviceInfo(identifiers={(DOMAIN, self._identifier)}, name=name,
                              manufacturer="Inovonics")
        device = self.runtime.devices.get(self.uid, {})
        point = self.runtime.points.get(self.uid, {}).get(self.suffix, {})
        model = device.get("model") or point.get("model") or "Unknown"
        return DeviceInfo(
            identifiers={(DOMAIN, self._identifier)}, manufacturer="Inovonics",
            name=f"{model} - {int(self.uid[2:], 16)}", model=model,
            serial_number=str(int(self.uid[2:], 16)),
        )

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self.runtime.subscribe(self.push))

    def push(self):
        state = self.state, self.available
        if state != self.last_state:
            self.last_state = state
            self.async_write_ha_state()
