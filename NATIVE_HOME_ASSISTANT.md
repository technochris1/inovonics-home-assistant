# Native Inovonics integration

The integration runs in Home Assistant Core and connects directly to an EchoStream coordinator over TCP or local serial (8N1). No MQTT broker or bridge is required. Use one connection method per coordinator.

## Install through Home Assistant Marketplace

On Home Assistant 2026.11 or newer:

1. Open **Settings → Marketplace → Add from link**.
2. Add `https://github.com/technochris1/inovonics-home-assistant`.
3. Open **Inovonics EchoStream** and select **Install**. Restart Home Assistant if prompted.
4. For a new setup, go to **Settings → Devices & services → Add integration → Inovonics EchoStream**, then enter the coordinator connection details.

For an existing manual installation, keep its configured Home Assistant integration entry. Marketplace installs into the existing integration directory and will show future releases as updates.

## Install through HACS on older Home Assistant versions

1. Open **HACS → ⋮ → Custom repositories**.
2. Add `https://github.com/technochris1/inovonics-home-assistant` with type **Integration**.
3. Open **Inovonics EchoStream** in HACS and select **Download**.
4. Restart Home Assistant. For a new setup, go to **Settings → Devices & services → Add integration → Inovonics EchoStream**, then choose TCP or serial and enter the coordinator connection details.

For an existing manual installation, follow the same HACS download and restart steps and keep the existing Home Assistant integration entry. Future releases appear in HACS, where you can download them and restart Home Assistant to apply them.

## Install manually

Copy the inovonics_echostream folder under custom_components into Home Assistant's configuration directory. Restart Home Assistant Core. Go to **Settings → Devices & services → Add integration → Inovonics EchoStream**, select the transport and enter the hardware settings. For TCP, use the coordinator or gateway LAN address and configured TCP port. For serial, use the device path reported in **Settings → System → Hardware**.

Open the integration's options to set default endpoint and repeater offline windows, supported-point filtering, or the debug mode and allowlisted client. A zero offline window disables the connectivity check. Debug is off by default. Receive mirrors raw RX, passthrough also forwards client TX, and trace emits direction-tagged hex lines. Passthrough pauses the integration's own coordinator commands while a debug client is attached.

The coordinator device reports the current NID and the latest NID operation. Under **Developer tools → Actions**, use **Set coordinator NID**, **Apply coordinator NID to a repeater**, or **Set device offline timeout**. Coordinator writes require confirmation and matching readback. Repeater changes apply the coordinator NID and require a matching report from the addressed repeater. NID changes can interrupt RF traffic. Use a maintenance window.

## Coordinator watchdog

In the integration options, set **Reconnect after coordinator silence** and
**Notify after the coordinator stays offline**. The defaults are 300 seconds
of silence and 180 seconds after a detected outage. Set either value to 0 to
disable that feature. Retries continue while the coordinator is offline.
The coordinator Connectivity sensor stays off during retries and returns to
connected after receiving data. **Last coordinator data** shows the last
received bytes. The persistent Home Assistant notification clears on recovery.

The watchdog also runs while a passthrough client is attached. The client
supplies any coordinator queries while attached. Set the silence interval
above the longest expected gap in incoming traffic. When the coordinator
link drops or the watchdog restarts it, the debug client is disconnected and
can reconnect to the same port.

## Migrate from MQTT

Under **Developer tools → Actions**, run **Preview MQTT entity migration**.
Review the supported point count, then run **Migrate MQTT entities** with
confirmation. The migration matches hardware UIDs and raw points and reuses
the existing Home Assistant entity and device registry IDs. It can create
native entities for devices still waiting for a live check-in. Their state
is unknown until the integration hears them.

If an existing automation intentionally refers to a reserved or unsupported
raw point, list its legacy entity ID in **Explicit legacy raw points** in
both actions. This preserves the same raw bit the automation previously used.
Other unsupported points stay on MQTT. Disabled MQTT placeholders prevent
retained discovery from recreating a competing entity after migration.

**Clean up MQTT migration duplicates** previews or removes disabled sensor
copies and duplicate device records left after migration. Run it with
**Apply cleanup** off to see what it found, then turn it on to apply the
cleanup. It groups remaining MQTT-only points under **Inovonics legacy MQTT
points** and preserves their entity IDs and state topics. Keep the MQTT
bridge stopped so it cannot republish the old discovery configurations.

Stop the standalone Inovonics MQTT bridge before migrating its entities.
Keep the MQTT integration running for devices that still use it.
