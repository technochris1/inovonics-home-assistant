from __future__ import annotations

from inovonics_echostream_processor import security_event_to_bit_state_updates
from inovonics_echostream_processor.events import SecurityMessageEvent


DEVICE_CLASSES = {
    "case_wall_tamper": "tamper",
    "eol_tamper": "tamper",
    "low_battery": "battery",
    "clean_me": "problem",
}
DEFAULT_POINT_LABELS = {
    (0, 4): "Reset",
    (0, 5): "Idle",
    (0, 6): "Case Tamper",
    (0, 7): "Battery Low",
    (1, 1): "Alarm 1",
    (1, 2): "Alarm 2",
    (1, 3): "Alarm 3",
    (1, 4): "Alarm 4",
}
STATION_CODE_POINTS = {(1, 1), (1, 2), (0, 6)}


def discovery_topic(discovery_prefix: str, device_uid_hex: str) -> str:
    return f"{discovery_prefix}/device/{device_uid_hex}/config"


def state_topic(state_prefix: str, device_uid_hex: str, group: int, bit: int) -> str:
    return f"{state_prefix}/{device_uid_hex}/{group}/{bit}/state"


def bool_to_state(flag: bool) -> str:
    return "ON" if flag else "OFF"


def station_state_topic(state_prefix: str, device_uid_hex: str, alarm_number: int) -> str:
    return f"{state_prefix}/{device_uid_hex}/station/{alarm_number}/state"


def iter_bit_updates(event: SecurityMessageEvent, *, hide_reserved_events: bool, station_mode: bool = False):
    for update in security_event_to_bit_state_updates(event):
        point = (update.stat_group, update.bit)
        if hide_reserved_events:
            if update.reserved or update.supported is False:
                continue
            if update.supported is None and point not in DEFAULT_POINT_LABELS:
                continue
            if station_mode and point in STATION_CODE_POINTS:
                continue
        yield update


def _binary_sensor(name: str, unique_id: str, topic: str) -> dict[str, str]:
    return {
        "p": "binary_sensor",
        "name": name,
        "unique_id": unique_id,
        "state_topic": topic,
        "payload_on": "ON",
        "payload_off": "OFF",
    }


def build_discovery_payload(
    event: SecurityMessageEvent,
    *,
    state_prefix: str,
    app_name: str,
    app_version: str,
    hide_reserved_events: bool = True,
    station_mode: bool = False,
) -> dict[str, object]:
    uid = event.device_uid_hex
    components: dict[str, dict[str, str]] = {}
    for update in iter_bit_updates(event, hide_reserved_events=hide_reserved_events, station_mode=station_mode):
        group, bit = update.stat_group, update.bit
        name = update.status_label or DEFAULT_POINT_LABELS.get((group, bit), "Reserved")
        component = _binary_sensor(name, f"{uid}.STAT{group}.{bit}", state_topic(state_prefix, uid, group, bit))
        if update.status_name in DEVICE_CLASSES:
            component["device_class"] = DEVICE_CLASSES[update.status_name]
        components[f"{uid}_{group}_{bit}"] = component

    if station_mode:
        for alarm_number in range(1, 8):
            components[f"{uid}_station_{alarm_number}"] = _binary_sensor(
                f"Alarm {alarm_number}",
                f"{uid}.ICN3000.ALARM{alarm_number}",
                station_state_topic(state_prefix, uid, alarm_number),
            )

    return {
        "dev": {
            "ids": uid,
            "name": f"{event.device_model} - {event.device_serial_number}",
            "mdl": event.device_model,
            "mf": "Inovonics",
            "sn": str(event.device_serial_number),
        },
        "o": {"name": app_name, "sw": app_version},
        "cmps": components,
    }


def discovery_removals(device_uid_hex: str, active_components):
    """Supply Home Assistant removal markers for previously published points."""
    raw_ids = {f"{device_uid_hex}_{group}_{bit}" for group in (0, 1) for bit in range(1, 9)}
    station_ids = {f"{device_uid_hex}_station_{alarm}" for alarm in range(1, 8)}
    return {key: {"p": "binary_sensor"} for key in sorted((raw_ids | station_ids) - active_components.keys())}


def iter_state_messages(
    event: SecurityMessageEvent,
    *,
    state_prefix: str,
    hide_reserved_events: bool = True,
):
    for update in iter_bit_updates(event, hide_reserved_events=hide_reserved_events):
        yield topic_and_payload_for_bit_state_update(update, state_prefix=state_prefix)


def topic_and_payload_for_bit_state_update(update, *, state_prefix: str):
    return (
        state_topic(state_prefix, update.device_uid_hex, update.stat_group, update.bit),
        bool_to_state(update.value),
    )
