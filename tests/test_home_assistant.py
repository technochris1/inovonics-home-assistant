from inovonics_echostream_processor.events import SecurityMessageEvent

from inovonics_python_app.home_assistant import (
    build_discovery_payload,
    discovery_topic,
    iter_state_messages,
    topic_and_payload_for_bit_state_update,
)


def _event() -> SecurityMessageEvent:
    return SecurityMessageEvent(
        raw_message=b"\x72",
        device_uid_hex="B2010203",
        device_serial_number=66051,
        first_hop_hex="11223344",
        trace_count=0,
        hop_count=1,
        mid=178,
        pti=4,
        device_category="SECURITY",
        device_model="EN1210SK",
        signal_level=100,
        signal_margin=50,
        stat1_flags=(True, False, False, True, False, False, False, False),
        stat0_flags=(False, False, False, True, False, True, False, False),
    )


def test_build_discovery_payload() -> None:
    payload = build_discovery_payload(
        _event(),
        state_prefix="inovonics",
        app_name="inovonics-python-app",
        app_version="1.0.0",
    )

    assert payload["dev"]["ids"] == "B2010203"
    assert payload["dev"]["mdl"] == "EN1210SK"
    alarm = payload["cmps"]["B2010203_1_1"]
    assert alarm["unique_id"] == "B2010203.STAT1.1"
    assert alarm["state_topic"] == "inovonics/B2010203/1/1/state"
    assert "B2010203_1_4" not in payload["cmps"]
    assert payload["cmps"]["B2010203_0_6"]["device_class"] == "tamper"


def test_discovery_topic_and_state_messages() -> None:
    topic = discovery_topic("homeassistant", "B2010203")
    messages = list(iter_state_messages(_event(), state_prefix="inovonics"))

    assert topic == "homeassistant/device/B2010203/config"
    assert messages[0] == ("inovonics/B2010203/1/1/state", "ON")
    assert len(messages) == 5
    assert messages[-1] == ("inovonics/B2010203/0/7/state", "OFF")


def test_reserved_events_can_be_published() -> None:
    messages = list(
        iter_state_messages(
            _event(),
            state_prefix="inovonics",
            hide_reserved_events=False,
        )
    )

    assert len(messages) == 16
    assert ("inovonics/B2010203/1/5/state", "OFF") in messages


def test_topic_and_payload_for_bit_state_update() -> None:
    topic, payload = topic_and_payload_for_bit_state_update(
        type(
            "Update",
            (),
            {
                "device_uid_hex": "B2010203",
                "stat_group": 0,
                "bit": 6,
                "value": True,
            },
        )(),
        state_prefix="inovonics",
    )

    assert topic == "inovonics/B2010203/0/6/state"
    assert payload == "ON"

def test_model_specific_bits_use_existing_numbering() -> None:
    event = _event()
    event.device_model = "EN1243"
    payload = build_discovery_payload(event, state_prefix="inovonics", app_name="test", app_version="1")
    clean_me = payload["cmps"]["B2010203_1_7"]
    assert clean_me["unique_id"] == "B2010203.STAT1.7"
    assert clean_me["state_topic"] == "inovonics/B2010203/1/7/state"
    assert clean_me["device_class"] == "problem"
    assert "B2010203_0_6" not in payload["cmps"]

    event.device_model = "EN1210EOL"
    payload = build_discovery_payload(event, state_prefix="inovonics", app_name="test", app_version="1")
    assert payload["cmps"]["B2010203_1_8"]["device_class"] == "tamper"


def _mqtt_bridge(published, *, coalescing=False, stations=()):
    from inovonics_python_app.config import load_config
    from inovonics_python_app.mqtt_app import MqttBridgeApp

    config = load_config("example-config.yaml")
    config.bit_coalescing.enabled = coalescing
    config.en1941_7_devices = frozenset(stations)
    bridge = MqttBridgeApp(config)
    bridge._mqtt_connected.set()
    bridge.publish = lambda topic, payload, *, retain: published.append((topic, payload, retain))
    return bridge


def test_aggregate_child_uses_the_same_mqtt_topics_as_direct_frames() -> None:
    import json

    aggregate = bytes.fromhex("72 1B 01 9D 10 BE 00 FA D0 2C 00 00 02 01 0B B2 43 E3 D7 3E 20 02 20 43 41 2E 2E 0C")
    for coalescing in (False, True):
        published = []
        bridge = _mqtt_bridge(published, coalescing=coalescing)
        envelope, _ = bridge.processor.core.feed(aggregate).events
        bridge._flush_buffered_bit_updates(flush_all=True)

        configs = [(json.loads(payload), retain) for topic, payload, retain in published if topic.endswith("/config")]
        assert [retain for _, retain in configs] == [False, True]
        removal, discovery = (config["cmps"] for config, _ in configs)
        assert removal["B243E3D7_1_4"] == {"p": "binary_sensor"}
        assert "B243E3D7_1_4" not in discovery
        assert discovery["B243E3D7_1_2"]["unique_id"] == "B243E3D7.STAT1.2"
        assert discovery["B243E3D7_1_2"]["state_topic"] == "inovonics/B243E3D7/1/2/state"
        assert published.count(("inovonics/B243E3D7/1/2/state", "ON", True)) == 1
        assert all("019D10BE" not in topic for topic, _, _ in published)

        published.clear()
        bridge.processor.core.feed(envelope.children[0].raw_message)
        bridge._flush_buffered_bit_updates(flush_all=True)
        assert not any(topic.endswith("/config") for topic, _, _ in published)
        if not coalescing:
            assert ("inovonics/B243E3D7/1/2/state", "ON", True) in published


def test_station_calls_coexist_and_survive_zero_reports_until_reset() -> None:
    import json

    from inovonics_echostream_processor import EchoStreamProtocol

    published = []
    bridge = _mqtt_bridge(published, stations=("B2010203",))
    event = _event()
    event.device_model = "EN1941"

    def handle(stat1, stat0):
        event.stat1_flags = EchoStreamProtocol.flags_lsb_first(stat1)
        event.stat0_flags = EchoStreamProtocol.flags_lsb_first(stat0)
        bridge.handle_processor_event(event)

    handle(1, 0)
    handle(0, 0x20)
    handle(0, 0)
    configs = [json.loads(payload) for topic, payload, retain in published if topic.endswith("/config") and retain]
    components = configs[0]["cmps"]
    assert "B2010203_1_1" not in components  # Raw code input.
    assert components["B2010203_0_7"]["unique_id"] == "B2010203.STAT0.7"  # Battery.
    assert components["B2010203_station_3"]["unique_id"] == "B2010203.ICN3000.ALARM3"
    assert [(topic, value) for topic, value, _ in published if "/station/" in topic] == [
        ("inovonics/B2010203/station/1/state", "ON"),
        ("inovonics/B2010203/station/3/state", "ON"),
    ]

    published.clear()
    bridge._mqtt_connected.clear()
    handle(3, 0x28)  # RESET takes precedence over code bits.
    bridge._mqtt_connected.set()
    bridge._flush_station_updates()
    assert [(topic, value) for topic, value, _ in published if "/station/" in topic] == [
        (f"inovonics/B2010203/station/{point}/state", "OFF") for point in range(1, 8)
    ]
