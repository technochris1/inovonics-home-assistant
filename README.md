# Inovonics EchoStream for Home Assistant

An independent Home Assistant integration for compatible Inovonics EchoStream
receivers. It connects directly over TCP or serial and creates devices and
sensor entities in Home Assistant.

For hardware photos and specifications, see the
[Inovonics EN4200 product page](https://www.inovonics.com/products/security-only-serial-receiver-en4200/).

For a new setup, open **Settings → Devices & services → Add integration**
and select **Inovonics EchoStream**. See the
[native setup and migration guide](NATIVE_HOME_ASSISTANT.md) for connection
settings and instructions for keeping an existing integration entry.

## Install the native integration

On Home Assistant 2026.11 or newer, add this repository in **Settings →
Marketplace → Add from link**. On earlier Home Assistant versions with HACS,
add it as a custom repository of type **Integration**. See the
[native installation guide](NATIVE_HOME_ASSISTANT.md) for both paths.
