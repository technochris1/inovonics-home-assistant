"""Consolidate duplicate device and entity records after MQTT migration."""

import asyncio
import json
from collections import defaultdict

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .migration import RAW_POINT

ARCHIVE = ("mqtt", "inovonics_legacy_mqtt")
ARCHIVE_INFO = {"ids": [ARCHIVE[1]], "name": "Inovonics legacy MQTT points", "mf": "Inovonics"}


async def cleanup(hass, entry, confirm):
    registry = er.async_get(hass)
    devices = dr.async_get(hass)
    journal = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.migration_journal")
    saved = await journal.async_load() or {"runs": []}
    migrated = defaultdict(dict)
    placeholder_ids = set()
    for run in saved["runs"]:
        if run["status"] != "migrated":
            continue
        placeholder_ids.update(run.get("mqtt_placeholders", ()))
        for old in run["entities"]:
            entity = registry.async_get(old["entity_id"])
            raw = RAW_POINT.fullmatch(old["unique_id"])
            if (raw and entity and entity.platform == DOMAIN and entity.config_entry_id == entry.entry_id
                    and entity.id == old["registry_id"] and entity.device_id == old["device_id"]):
                migrated[raw[1].upper()][old["unique_id"]] = entity

    parked = []
    for entity in registry.entities.values():
        suffix = f".superseded.{entity.id}"
        if entity.platform != DOMAIN or entity.config_entry_id != entry.entry_id or not entity.disabled:
            continue
        if not entity.unique_id.endswith(suffix):
            continue
        point = entity.unique_id.removesuffix(suffix)
        raw = RAW_POINT.fullmatch(point)
        if raw and any(other.unique_id == point for other in migrated.get(raw[1].upper(), {}).values()):
            parked.append(entity)

    native_copies = [
        device for uid in migrated
        if (device := devices.async_get_device_by_identifier((DOMAIN, f"{uid}.superseded"), entry.entry_id))
        and not er.async_entries_for_device(registry, device.id, include_disabled_entities=True)
    ]
    mqtt_entries = [item for item in hass.config_entries.async_entries("mqtt")
                    if item.state is ConfigEntryState.LOADED]
    shadows = {}
    placeholders = {}
    if len(mqtt_entries) == 1:
        mqtt_entry = mqtt_entries[0]
        for device in dr.async_entries_for_config_entry(devices, mqtt_entry.entry_id):
            for domain, value in device.identifiers:
                uid = value.partition(".legacy.")[0]
                if domain == "mqtt" and uid in migrated and len(device.identifiers) == 1:
                    shadows[device.id] = device
        for points in migrated.values():
            for key in points:
                row_id = registry.async_get_entity_id("binary_sensor", "mqtt", key)
                if row_id in placeholder_ids and (row := registry.async_get(row_id)):
                    if row.config_entry_id == mqtt_entry.entry_id and row.disabled:
                        placeholders[key] = row

    report = {
        "disabled_native_copies": len(parked),
        "empty_native_devices": len(native_copies),
        "duplicate_mqtt_devices": len(shadows),
        "disabled_mqtt_copies": len(placeholders),
    }
    if not confirm:
        return report

    if shadows or placeholders:
        await consolidate_mqtt(hass, mqtt_entry, migrated, shadows, placeholders, entry.entry_id)
    for entity in parked:
        registry.async_remove(entity.entity_id)
    for device in native_copies:
        devices.async_remove_device(device.id)
    return report


async def consolidate_mqtt(hass, mqtt_entry, migrated, shadows, placeholders, native_entry_id):
    from homeassistant.components import mqtt

    registry = er.async_get(hass)
    devices = dr.async_get(hass)
    if not mqtt.is_connected(hass):
        raise ServiceValidationError("Connect to the MQTT broker before cleaning up migration duplicates.")
    prefix = mqtt_entry.options.get("discovery_prefix", "homeassistant")
    topics = {f"{prefix}/device/{uid}/config": uid for uid in migrated}
    payloads = {}

    @callback
    def collect(message):
        if message.topic in topics and message.retain and message.payload:
            payloads[message.topic] = message.payload

    unsubscribe = await mqtt.async_subscribe(hass, f"{prefix}/device/+/config", collect, qos=1)
    try:
        # Some migrated devices no longer have retained discovery. Allow time
        # for the broker to replay the configs that still exist.
        await asyncio.sleep(4)
    finally:
        unsubscribe()
    if not mqtt.is_connected(hass):
        raise ServiceValidationError("The MQTT broker disconnected during discovery cleanup.")

    original_rows = {
        row.entity_id: row for shadow in shadows.values()
        for row in er.async_entries_for_device(registry, shadow.id, include_disabled_entities=True)
    }
    if any(row.platform != "mqtt" or row.config_entry_id != mqtt_entry.entry_id
           for row in original_rows.values()):
        raise ServiceValidationError("An Inovonics legacy device contains unexpected entities.")

    replacements = {}
    for topic, payload in payloads.items():
        uid = topics[topic]
        try:
            config = json.loads(payload)
            device_key = "dev" if "dev" in config else "device"
            components_key = "cmps" if "cmps" in config else "components"
            device = config[device_key]
            identifiers = device.get("ids", device.get("identifiers"))
            if identifiers not in (uid, [uid], [ARCHIVE[1]]):
                raise ValueError("Unexpected MQTT device identity")
            components = config[components_key]
            removal = {}
            remaining = {}
            for key, component in components.items():
                platform = component.get("p", component.get("platform"))
                unique_id = component.get("uniq_id", component.get("unique_id"))
                if component.keys() in ({"p"}, {"platform"}):
                    continue
                if platform == "binary_sensor" and unique_id in placeholders and unique_id in migrated[uid]:
                    removal[key] = {"p": platform}
                else:
                    remaining[key] = component
            if removal or identifiers != [ARCHIVE[1]] or len(remaining) != len(components):
                removal_config = {**config, components_key: {**remaining, **removal}}
                final = {**config, device_key: ARCHIVE_INFO, components_key: remaining}
                replacements[topic] = (
                    json.dumps(removal_config) if removal else None,
                    json.dumps(final) if remaining else "",
                )
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            raise ServiceValidationError(f"Unexpected retained Inovonics config for {uid}.") from error

    if replacements:
        backup = Store(hass, 1, f"{DOMAIN}.{native_entry_id}.mqtt_cleanup")
        earlier = await backup.async_load() or {}
        await backup.async_save({**payloads, **earlier})
    for topic, (removal, final) in replacements.items():
        if removal:
            await mqtt.async_publish(hass, topic, removal, qos=1, retain=True)
        await mqtt.async_publish(hass, topic, final, qos=1, retain=True)

    # Home Assistant may already have removed a discovered row. Journaled
    # placeholders it never loaded can still be present after broker cleanup.
    for row in placeholders.values():
        if registry.async_get(row.entity_id) == row:
            registry.async_remove(row.entity_id)

    survivors = [row for original in original_rows.values()
                 if (row := registry.async_get(original.entity_id)) and row.id == original.id]
    if survivors:
        archive = devices.async_get_or_create(
            config_entry_id=mqtt_entry.entry_id, identifiers={ARCHIVE},
            name=ARCHIVE_INFO["name"], manufacturer=ARCHIVE_INFO["mf"],
        )
        for row in survivors:
            if row.device_id != archive.id:
                registry.async_update_entity(row.entity_id, device_id=archive.id)
    for shadow in shadows.values():
        if (devices.async_get(shadow.id)
                and not er.async_entries_for_device(registry, shadow.id, include_disabled_entities=True)):
            devices.async_remove_device(shadow.id)
