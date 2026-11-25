"""Adopt legacy Inovonics MQTT registry entries, retaining their IDs."""

import asyncio
from dataclasses import dataclass
import re

from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.entity import entity_sources
from homeassistant.helpers.storage import Store
from inovonics_echostream_processor import security_status_bit_definition, security_status_capabilities

from .const import DOMAIN
from .runtime import CLASSES

RAW_POINT = re.compile(r"([0-9A-F]{8})\.STAT([01])\.([1-8])", re.IGNORECASE)
PARKED_SUFFIX = "_mqtt_legacy"


@dataclass
class Point:
    old: er.RegistryEntry
    device: dr.DeviceEntry
    uid: str
    suffix: str
    info: dict
    pin_raw: bool

    @property
    def key(self):
        return f"{self.uid}.{self.suffix}"


def plan(hass, runtime, explicit):
    registry = er.async_get(hass)
    devices = dr.async_get(hass)
    candidates, skipped = [], []
    eligible_extra = set()
    for old in tuple(registry.entities.values()):
        if old.domain != "binary_sensor" or old.platform != "mqtt" or PARKED_SUFFIX in old.entity_id:
            continue
        raw = RAW_POINT.fullmatch(old.unique_id)
        device = devices.async_get(old.device_id) if old.device_id else None
        if not raw or not device:
            continue
        uid = raw[1].upper()
        if not any(domain == "mqtt" and value.upper() == uid for domain, value in device.identifiers):
            continue
        eligible_extra.add(old.entity_id)
        suffix = old.unique_id.split(".", 1)[1].upper()
        model = device.model or "Unknown"
        pin = old.entity_id in explicit
        group, bit = int(raw[2]), int(raw[3])
        flags = security_status_capabilities(model)
        definition = security_status_bit_definition(group, bit - 1, model)
        supported = bool(definition) and (flags is not None and (group, bit - 1) in flags
                                           or flags is None and (group == 0 or bit <= 4))
        if not supported and not pin and runtime.entry.options.get("hide_unsupported", True):
            skipped.append(old.entity_id)
            continue
        name = (definition or {}).get("label") or f"Raw {suffix}"
        device_class = CLASSES.get((definition or {}).get("name"))
        candidates.append(Point(old, device, uid, suffix, {
            "name": name, "class": device_class, "model": model, "value": None, "filtered": False,
        }, pin))

    unknown = explicit - eligible_extra
    if unknown:
        raise ServiceValidationError("These are not legacy Inovonics MQTT entities: " + ", ".join(sorted(unknown)))
    for uid in {point.uid for point in candidates}:
        if len({point.device.id for point in candidates if point.uid == uid}) != 1:
            raise ServiceValidationError(f"Multiple MQTT devices claim transmitter {uid}.")
    return candidates, skipped


def summary(candidates, skipped, runtime):
    return {
        "eligible_entities": len(candidates),
        "devices": len({point.uid for point in candidates}),
        "with_native_readings": sum(point.suffix in runtime.points.get(point.uid, {})
                                    and runtime.points[point.uid][point.suffix].get("value") is not None
                                    for point in candidates),
        "unsupported_or_other_profile": len(skipped),
        "explicit_raw_points": [point.old.entity_id for point in candidates if point.pin_raw],
    }


async def migrate(hass, runtime, candidates):
    registry = er.async_get(hass)
    devices = dr.async_get(hass)
    entry = runtime.entry
    backups = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.migration_journal")
    previous = await backups.async_load() or {"runs": []}
    baseline = []
    for point in candidates:
        native = registry.async_get_entity_id("binary_sensor", DOMAIN, point.key)
        duplicate = devices.async_get_device_by_identifier((DOMAIN, point.uid), entry.entry_id)
        baseline.append({
            "entity_id": point.old.entity_id, "registry_id": point.old.id,
            "unique_id": point.old.unique_id, "device_id": point.device.id,
            "old_config_entry_id": point.old.config_entry_id, "disabled_by": point.old.disabled_by,
            "native_duplicate": native, "native_duplicate_device_id": duplicate.id if duplicate else None,
        })
    record = {"status": "prepared", "entities": baseline}
    previous["runs"].append(record)
    await backups.async_save(previous)

    if not await hass.config_entries.async_unload(entry.entry_id):
        raise ServiceValidationError("Could not unload the native entry before migration.")

    parked_duplicates = []
    moved = []
    placeholders = []
    device_changes = []
    reassigned_native = []
    reassigned_mqtt = []
    mqtt_devices = {}
    selected_ids = {point.old.entity_id for point in candidates}
    adopted_points = []
    original_points = {uid: values.copy() for uid, values in runtime.points.items()}
    original_pins = runtime.pinned_points.copy()
    try:
        # The registry's platform-migration API requires the MQTT entities to
        # be unloaded. Disable only the rows selected above and wait for that.
        for point in candidates:
            if point.old.disabled_by is None:
                registry.async_update_entity(point.old.entity_id, disabled_by=er.RegistryEntryDisabler.USER)
        for _ in range(60):
            if not any(point.old.entity_id in entity_sources(hass) for point in candidates):
                break
            await asyncio.sleep(0.1)
        else:
            raise ServiceValidationError("MQTT did not release the selected entities in time.")

        for point in candidates:
            old = point.old
            duplicate_id = registry.async_get_entity_id("binary_sensor", DOMAIN, point.key)
            if duplicate_id:
                duplicate = registry.async_get(duplicate_id)
                alternate = f"{point.key}.superseded.{duplicate.id}"
                registry.async_update_entity(duplicate.entity_id, new_unique_id=alternate,
                                             disabled_by=er.RegistryEntryDisabler.USER)
                parked_duplicates.append(duplicate)
            result = registry.async_update_entity_platform(
                old.entity_id, DOMAIN, new_config_entry_id=entry.entry_id, new_config_subentry_id=None,
                new_device_id=None,
                **({"new_unique_id": point.key} if point.key != old.unique_id else {}),
            )
            moved.append(point)
            assert result.id == old.id and result.entity_id == old.entity_id


        for point in {item.uid: item for item in candidates}.values():
            old = point.device
            identity = (DOMAIN, point.uid)
            duplicate = devices.async_get_device_by_identifier(identity, entry.entry_id)
            change = [old, None, None]
            device_changes.append(change)
            if duplicate and duplicate.id != old.id:
                change[1] = duplicate
                devices.async_update_device(duplicate.id, new_identifiers=(duplicate.identifiers - {identity})
                                            | {(DOMAIN, f"{point.uid}.superseded")})
            if old.config_entry_id != entry.entry_id:
                shadow = devices.async_get_or_create(
                    config_entry_id=old.config_entry_id, config_subentry_id=old.config_subentry_id,
                    identifiers={("mqtt", f"{point.uid}.legacy.{old.id}")},
                    name=f"{old.name_by_user or old.name or 'Inovonics'} (MQTT legacy)",
                    manufacturer=old.manufacturer, model=old.model,
                )
                change[2] = shadow
                mqtt_devices[old.id] = shadow.id
                for other in er.async_entries_for_device(registry, old.id, include_disabled_entities=True):
                    if other.platform == "mqtt" and other.entity_id not in selected_ids:
                        registry.async_update_entity(other.entity_id, device_id=shadow.id)
                        reassigned_mqtt.append((other.entity_id, old.id))
                devices.async_update_device(
                    old.id, new_config_entry_id=entry.entry_id, new_config_subentry_id=None,
                    new_identifiers={identity},
                )
                devices.async_update_device(shadow.id, new_identifiers=old.identifiers)
            else:
                devices.async_update_device(old.id, new_identifiers=old.identifiers | {identity})
            if duplicate and duplicate.id != old.id:
                for existing in er.async_entries_for_device(registry, duplicate.id, include_disabled_entities=True):
                    registry.async_update_entity(existing.entity_id, device_id=old.id)
                    reassigned_native.append((existing.entity_id, duplicate.id))

        for point in moved:
            old = point.old
            registry.async_update_entity(old.entity_id, device_id=point.device.id, disabled_by=old.disabled_by)
            mqtt_entry = hass.config_entries.async_get_entry(old.config_entry_id) if old.config_entry_id else None
            suffix = old.entity_id.partition(".")[2] + PARKED_SUFFIX
            parked = registry.async_get_or_create(
                "binary_sensor", "mqtt", old.unique_id, config_entry=mqtt_entry,
                device_id=mqtt_devices.get(point.device.id, point.device.id),
                disabled_by=er.RegistryEntryDisabler.USER, suggested_object_id=suffix,
            )
            if not parked.disabled:
                registry.async_update_entity(parked.entity_id, disabled_by=er.RegistryEntryDisabler.USER)
            placeholders.append(parked.entity_id)
            adopted_points.append(point)

        for point in adopted_points:
            if point.pin_raw:
                runtime.pinned_points.add(point.key)
            points = runtime.points.setdefault(point.uid, {})
            points.setdefault(point.suffix, point.info)
        await runtime.save(strict=True)
        record["status"] = "migrated"
        record["mqtt_placeholders"] = placeholders
        await backups.async_save(previous)
    except Exception:
        for entity_id in reversed(placeholders):
            registry.async_remove(entity_id)
        for entity_id, device_id in reversed(reassigned_native):
            registry.async_update_entity(entity_id, device_id=device_id)
        for point in reversed(moved):
            registry.async_update_entity(point.old.entity_id, device_id=None)
        for old, duplicate, shadow in reversed(device_changes):
            if shadow:
                devices.async_update_device(shadow.id, new_identifiers=shadow.identifiers)
            devices.async_update_device(old.id, new_identifiers=old.identifiers,
                                        new_config_entry_id=old.config_entry_id,
                                        new_config_subentry_id=old.config_subentry_id)
            if duplicate:
                devices.async_update_device(duplicate.id, new_identifiers=duplicate.identifiers)
        for point in reversed(moved):
            old = point.old
            registry.async_update_entity_platform(
                old.entity_id, "mqtt", new_config_entry_id=old.config_entry_id,
                new_config_subentry_id=old.config_subentry_id, new_device_id=old.device_id,
                **({"new_unique_id": old.unique_id} if point.key != old.unique_id else {}),
            )
        for entity_id, device_id in reversed(reassigned_mqtt):
            registry.async_update_entity(entity_id, device_id=device_id)
        for duplicate in reversed(parked_duplicates):
            registry.async_update_entity(duplicate.entity_id, new_unique_id=duplicate.unique_id,
                                         disabled_by=duplicate.disabled_by)
        for point in candidates:
            registry.async_update_entity(point.old.entity_id, disabled_by=point.old.disabled_by)
        runtime.points = original_points
        runtime.pinned_points = original_pins
        await runtime.save()
        record["status"] = "rolled_back"
        await backups.async_save(previous)
        raise
    finally:
        await hass.config_entries.async_setup(entry.entry_id)

    return {"migrated_entities": len(moved), "mqtt_legacy_entities_disabled": len(placeholders),
            "devices": len(device_changes)}
