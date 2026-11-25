# Retired Inovonics EchoStream MQTT bridge

The Home Assistant OS bridge app and standalone Docker bridge are retired.
The Home Assistant App Store repository no longer offers the bridge, and
standalone Docker image publishing has ended.

The supported Home Assistant setup is the
[native Inovonics EchoStream integration](NATIVE_HOME_ASSISTANT.md). It
connects directly to the receiver over TCP or serial.

For an existing Home Assistant setup, stop the bridge before connecting the
native integration to the same receiver. The
[migration guide](NATIVE_HOME_ASSISTANT.md#migrate-from-mqtt) explains how to
preserve supported entity IDs.

The [historical bridge documentation](https://github.com/technochris1/inovonics-home-assistant/blob/1.2.5/MQTT_BRIDGE.md)
and [previous Docker images](https://hub.docker.com/r/technochris1/inovonicspythonapp)
are available for reference.
