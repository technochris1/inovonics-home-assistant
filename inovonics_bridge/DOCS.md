# Inovonics EchoStream Bridge

Use this app with Home Assistant OS and the [MQTT integration](https://www.home-assistant.io/integrations/mqtt/). The bridge connects through TCP or a serial device attached to Home Assistant OS. The app runs on x86-64 and 64-bit ARM and builds on first install.

## Set up

1. In **Configuration**, choose **TCP** and enter the coordinator address and TCP port, or choose **Serial** and select the local serial device and matching baud rate (8N1). Save and restart after changing the connection.
2. If Home Assistant uses the Mosquitto broker app, leave **MQTT broker host** unset. Supervisor supplies the broker settings automatically. For an external broker, enter the host, port, and any credentials. The bridge uses MQTT 3.1.1 over a plain TCP connection.
3. Set up Home Assistant's MQTT integration with the same broker and discovery prefix. Start the bridge app and check its **Log** tab for the broker connection and EchoStream traffic.
4. To expose seven independent calls for an ICN 3000 station in seven-code mode, enter its full eight-digit EchoStream UID.

## Manage the coordinator and offline settings

Select **Open Web UI**. The panel shows the connection state, the last reported coordinator NID, and repeaters and devices heard by the bridge.

- Enter a coordinator NID from 0 through 31 and confirm the change. NID 0 is broadcast. The panel requires a matching coordinator readback to verify the change.
- Use **Apply coordinator NID** on a repeater heard on the current connection. The EchoStream command copies the coordinator's NID to that specific repeater. A coordinator ACK means the command was accepted. Confirmation requires a reset report from the selected repeater showing the matching NID.
- Set separate default offline windows for endpoints and repeaters, with optional per-UID overrides. The default is 120 minutes. Use 0 to disable monitoring and leave an override blank to inherit its default. Last heard times and supervision settings survive restarts. The MQTT connectivity sensors are separate from the existing alarm entities.

Plan coordinator changes for a maintenance window. A change can interrupt repeater traffic until their NIDs match. The app sends one requested write at a time and does not automatically repeat unverified writes. A serial receiver without coordinator NID support can still receive device traffic.

## Debug socket

The socket is off by default. Select a mode and enter the allowed client IPv4 address or a trusted subnet in **Configuration**, then enable the port mapping under **Network** and restart the app. Connect to the Home Assistant host and the port you mapped from container TCP port 19001. The app accepts one debug client at a time.

- **Receive** mirrors the exact bytes received from the coordinator. Client writes are rejected.
- **Passthrough** mirrors the same bytes and forwards your client's bytes to the active TCP or serial connection. While attached, the bridge pauses its own coordinator commands and continues decoding incoming events for MQTT. Disconnect the client to resume app commands.
- **Trace** sends one JSON object per observed read or write with direction rx or tx and the original bytes in hex. It reports actual traffic, including the app's outbound coordinator commands.

Use the diagnostic socket only on a trusted network. The raw protocol has no login or encryption, so the app filters clients by their source address. A slow debug client is disconnected so the MQTT bridge can continue processing.

## Move from the standalone bridge

Match your old MQTT broker, discovery prefix, state prefix, and seven-code device settings. Stop the standalone bridge before starting the app, especially if both are configured with the same MQTT client ID. Existing applicable points keep their MQTT unique IDs and topics. If you roll back, stop this app before restarting the standalone bridge.
