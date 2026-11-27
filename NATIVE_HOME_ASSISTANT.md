# Native Inovonics integration

The integration runs in Home Assistant Core and connects directly to an EchoStream coordinator over TCP or local serial (8N1). Use one connection method per coordinator.

## Install through Home Assistant Marketplace

On Home Assistant 2026.11 or newer:

1. Open **Settings → Marketplace** and search for **Inovonics EchoStream**.
2. Open the listing and select **Install**.
3. For a new setup, follow the prompts to enter the coordinator connection details.

## Install through HACS on older Home Assistant versions

1. Open **HACS** and search for **Inovonics EchoStream**.
2. Open the listing and select **Download**.
3. Restart Home Assistant. For a new setup, go to **Settings → Devices & services → Add integration → Inovonics EchoStream**, then choose TCP or serial and enter the coordinator connection details.

When switching from a manual installation to Marketplace or HACS, keep your
existing Home Assistant integration entry. Future releases appear as updates
in the store you use.

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
connected after receiving data. **Last coordinator data** shows when data last
arrived. The persistent Home Assistant notification clears on recovery.

The watchdog also runs while a passthrough client is attached. The client
supplies any coordinator queries while attached. Set the silence interval
above the longest expected gap in incoming traffic. When the coordinator
link drops or the watchdog restarts it, the debug client is disconnected and
can reconnect to the same port.
