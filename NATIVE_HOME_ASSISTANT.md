# Native Inovonics integration

The integration runs in Home Assistant Core and connects directly to EchoStream coordinators over TCP or local serial (8N1). Add one integration entry per physical coordinator. Setup reads the coordinator serial number to detect duplicates, even when two addresses reach the same coordinator.

## Install through Home Assistant Marketplace

On Home Assistant 2026.11 or newer:

1. Open **Settings → Marketplace** and search for **Inovonics EchoStream**.
2. Open the listing and select **Install**.
3. For a new setup, follow the prompts to enter the coordinator connection details.

To add another coordinator, repeat **Add integration** under **Settings → Devices & services**. Let your original coordinator connect and report its serial number first. If you enable the debug socket for multiple coordinators, give each one its own TCP port.

## Install through HACS on older Home Assistant versions

1. Open **HACS** and search for **Inovonics EchoStream**.
2. Open the listing and select **Download**.
3. Restart Home Assistant. For a new setup, go to **Settings → Devices & services → Add integration → Inovonics EchoStream**, then choose TCP or serial and enter the coordinator connection details.

When switching from a manual installation to Marketplace or HACS, keep your
existing Home Assistant integration entry. Future releases appear as updates
in the store you use.

## Install manually

Copy the inovonics_echostream folder under custom_components into Home Assistant's configuration directory. Restart Home Assistant Core. Go to **Settings → Devices & services → Add integration → Inovonics EchoStream**, select the transport and enter the hardware settings. For TCP, use the coordinator or gateway LAN address and configured TCP port. For serial, use the device path reported in **Settings → System → Hardware**.

Open **Configure → Integration settings** to set default endpoint and repeater offline windows, supported-point filtering, or the debug mode and allowlisted client. A zero offline window disables the connectivity check. Debug is off by default. Receive mirrors raw RX, passthrough also forwards client TX, and trace emits direction-tagged hex lines. Passthrough pauses the integration's own coordinator commands while a debug client is attached.

## Read and update network IDs

In **Settings → Devices & services → Inovonics EchoStream**, open **Configure → Coordinator NID** on the coordinator entry. Choose **Read coordinator NID** to query the connected hardware, or **Set coordinator NID** to read the current value, enter a new NID, and confirm the change. Home Assistant shows success once the coordinator reports the requested value. NID changes can interrupt RF traffic. Use a maintenance window.

The same read and set actions are also available under **Developer tools → Actions**, where you can select the coordinator. The coordinator device shows its last reported NID and the latest NID operation.

To update a repeater, open the coordinator's **Configure** menu in **Settings → Devices & services → Inovonics EchoStream** and select **Update repeater NID**.

1. Select the repeater. Open its enclosure and check that its jumper is in directed mode.
2. Continue in Home Assistant and press the repeater's reset button. If its reported NID differs from the coordinator's directed NID, the integration sends an update to that repeater.
3. When Home Assistant prompts you, press reset again. The flow completes when that same repeater reports the expected NID. If the first report already matched, the flow completes without sending an update.

## Remove a sensor or repeater

Open the device in Home Assistant and choose **Delete**. The integration forgets that device's cached data and supervision settings. A fresh radio report can discover it again. The coordinator stays in place.

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
