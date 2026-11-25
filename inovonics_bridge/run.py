"""Start the existing MQTT bridge with Home Assistant Supervisor options."""

import json
import os
import signal
from threading import Thread
import sys
from collections.abc import Mapping
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import Request, urlopen

from inovonics_python_app.config import load_config
from inovonics_python_app.mqtt_app import MqttBridgeApp, configure_diagnostic_hooks, configure_logging

from control import Coordinator, ManagedLink, Source
from panel import PanelServer
from debug_relay import DebugRelay


def supervisor_mqtt():
    token = os.getenv("SUPERVISOR_TOKEN")
    if not token:
        raise ValueError("Set MQTT broker host or run the app under Home Assistant Supervisor.")

    request = Request(
        "http://supervisor/services/mqtt",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urlopen(request, timeout=5) as response:
            service = json.load(response)["data"]
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise ValueError(
            "No MQTT broker was available from Supervisor. Start the Mosquitto broker app "
            "or enter MQTT broker settings in this app."
        ) from error

    if not isinstance(service, Mapping) or not service.get("host") or not service.get("port"):
        raise ValueError("The Supervisor MQTT service did not provide a host and port.")
    if service.get("ssl"):
        raise ValueError("The MQTT service requires TLS. This bridge requires a plain MQTT endpoint.")
    if service.get("protocol") not in (None, "3.1.1"):
        raise ValueError("The MQTT service must support MQTT 3.1.1.")
    return service


def bridge_config(options, data_dir):
    if not isinstance(options, dict):
        raise ValueError("App options must be a JSON object.")
    mqtt_host = (options.get("mqtt_host") or "").strip()
    if mqtt_host:
        broker = {
            "host": mqtt_host,
            "port": options["mqtt_port"],
            "username": options.get("mqtt_username"),
            "password": options.get("mqtt_password"),
        }
    else:
        broker = supervisor_mqtt()

    return {
        # MqttBridgeApp's default processor is replaced before it starts.
        "processor": {"host": "localhost", "port": 10001},
        "mqtt": {
            "broker": broker["host"],
            "port": broker["port"],
            "username": broker.get("username"),
            "password": broker.get("password"),
            "client_id": options["mqtt_client_id"],
            "discovery_prefix": options["discovery_prefix"],
            "state_prefix": options["state_prefix"],
            "hide_reserved_events": options["hide_reserved_events"],
        },
        "en1941_7_devices": options["en1941_7_devices"],
        "logging": {"file": str(data_dir / "bridge.log")},
    }


def stop_bridge(_signal, _frame):
    raise KeyboardInterrupt


def main(data_dir=Path("/data")):
    try:
        options = json.loads((data_dir / "options.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print("Cannot read the Home Assistant app options. Save the app configuration and restart it.", file=sys.stderr)
        return 1
    try:
        config = bridge_config(options, data_dir)
        source = Source.from_options(options)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    except (AttributeError, KeyError, TypeError):
        print("Invalid app options. Check the settings on the Configuration tab.", file=sys.stderr)
        return 1

    with TemporaryDirectory(prefix="inovonics-") as directory:
        config_path = Path(directory) / "bridge.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        app_config = load_config(config_path)

    configure_diagnostic_hooks()
    configure_logging(app_config.logging)
    bridge = MqttBridgeApp(app_config)
    control = Coordinator(source, data_dir)
    try:
        debug_mode = options.get("debug_mode", "off")
        relay = DebugRelay(control, debug_mode, options.get("debug_client")) if debug_mode != "off" else None
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    bridge.processor = ManagedLink(control, bridge, relay)
    with PanelServer(control) as panel:
        panel_thread = Thread(target=panel.serve_forever, name="ha-ingress", daemon=True)
        panel_thread.start()
        try:
            bridge.start()
            panel_thread.join()
        except KeyboardInterrupt:
            pass
        finally:
            panel.shutdown()
            bridge.stop()
    return 0


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, stop_bridge)
    raise SystemExit(main())
