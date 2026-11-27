# Inovonics EchoStream for Home Assistant

An independent Home Assistant integration for compatible Inovonics EchoStream
receivers. It connects directly over TCP or serial and creates devices and
sensor entities in Home Assistant.

For hardware photos and specifications, see the
[Inovonics EN6040-T Network Coordinator product page](https://www.inovonics.com/products/network-coordinator-en6040-t/).

## Install the native integration

On Home Assistant 2026.11 or newer, open **Settings → Marketplace**, search for
**Inovonics EchoStream**, and select **Install**. Follow the setup prompts to
connect your receiver.

On earlier Home Assistant versions with HACS, search for **Inovonics EchoStream**
in HACS and select **Download**. See the [native setup guide](NATIVE_HOME_ASSISTANT.md)
for connection settings and instructions for keeping an existing integration entry.

## License

The native integration is licensed under [GPL-3.0-only](LICENSE), also
included in the [integration package](custom_components/inovonics_echostream/LICENSE).
The separate
[EchoStream processor](https://github.com/technochris1/InovonicsEchostreamProcessor)
is also licensed under GPL-3.0-only.
