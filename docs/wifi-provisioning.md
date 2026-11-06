# StickS3 Wi-Fi setup: BLE + PoP / QR, with USB recovery

Amusant's Jellii firmware supports **Espressif BLE Wi-Fi provisioning (Security 1)**. The phone sends Wi-Fi SSID/password over a temporary authenticated BLE session. **It does not send the Relay address, TLS-PSK, device token, or Gemini/Grok credentials.** Configure those separately before flashing; see [self-hosting](self-hosting.md).

## Requirements

- StickS3 flashed with the **Amusant-patched**, privately configured firmware, not the compile-only CI binary.
- A nearby phone running a compatible **Espressif ESP BLE Provisioning** app (Security 1 / PoP support) with Bluetooth permission. Do **not** use a generic Bluetooth pairing screen.
- A 2.4 GHz Wi-Fi network supported by ESP32, its SSID and password.
- For first setup, USB power; for later owner setup, USB power or sufficient battery.

## First setup: no saved Wi-Fi profile

1. Connect USB power and boot a device that has **no saved Wi-Fi profile**.
2. The screen displays **Secure Wi-Fi setup** and a temporary **26-character Proof of Possession (PoP)**, split across two lines. **The first-boot screen does not display a QR code.**
3. Open the ESP BLE provisioning app, scan for a service named `PROV_XXXXXX`, select the StickS3 and enter the **exact PoP shown on its screen** when the app asks.
4. Select or enter the Wi-Fi SSID and Wi-Fi password; send them over the BLE provisioning session.
5. The device validates the connection and an assigned IP address **before saving** the profile. When provisioning ends, it attempts its normal Wi-Fi and Relay connection.

The first-boot BLE window lasts **120 seconds**. If it expires or fails, use the [USB fallback](#usb-serial-fallback) or reboot the device on USB power for another attempt. Do not assume a single successful BLE transmission means the Relay is reachable.

## Change Wi-Fi later: owner-triggered QR setup

1. From normal **Idle / Offline** state (not during speaking/listening), press and **hold side B for 5 seconds**. When the screen is off, wake it first, then perform the hold.
2. The device software-restarts into a dedicated setup session. On supported firmware it displays a **QR code** for the BLE service and current PoP.
3. With a compatible ESP provisioning app, scan that QR code. If QR handling is unavailable, press **front A** to display the PoP and select `PROV_XXXXXX` manually in the app. Press A to toggle QR/PoP pages.
4. Send the new Wi-Fi credentials, wait for the device to validate Wi-Fi plus IP, save the profile and return to normal mode. **B** cancels owner setup without replacing the saved Wi-Fi profile.

Owner-triggered setup is available for **180 seconds**. Without USB power, entry requires at least **20% battery**; setup exits if battery falls too low. The QR code contains a temporary BLE service name and PoP, **not the Wi-Fi password**. It is a provisioning-app QR payload, **not** a generic Wi-Fi QR code.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `PROV_XXXXXX` is not found | The device must currently be in its time-limited BLE setup session; check Bluetooth permissions, USB/charge, then retry |
| The app rejects proof | Use the fresh 26-character PoP currently shown on the device; it changes between sessions |
| BLE says submitted but Wi-Fi does not connect | Check SSID, password, network compatibility and whether the device receives an IP address |
| Repeated failures | BLE provisioning limits failed credential attempts; retry a fresh session or use USB |
| No QR on initial setup | Expected: the initial USB-powered, unconfigured boot shows the PoP text; QR appears during B-button owner setup |
| Device says reboot to retry BLE | Reboot/USB recover, then start a new provisioning session |
| Wi-Fi succeeds, but Jellii remains Offline | Check Relay FQDN, PSK/token, TLS-PSK ingress and server availability; BLE has not configured these |

BLE is intentionally disabled during normal voice and Relay operation.

## USB Serial fallback

The repository includes a USB-only provisioning tool at [`patches/kai-v0.12.0-wifi-w1/tools/provision_wifi.py`](../patches/kai-v0.12.0-wifi-w1/tools/provision_wifi.py).

Install its dependencies **on your computer** (not on the StickS3), identify the correct USB serial port, and run from the repository root:

```sh
python -m pip install -r patches/kai-v0.12.0-wifi-w1/tools/requirements.txt
python patches/kai-v0.12.0-wifi-w1/tools/provision_wifi.py --port /dev/ttyACM0 status
python patches/kai-v0.12.0-wifi-w1/tools/provision_wifi.py --port /dev/ttyACM0 set
```

On Windows, use the correct port such as `COM6` instead of `/dev/ttyACM0`. The `set` command interactively asks for the SSID and securely prompts for the password (not on the command line). To remove an existing Wi-Fi profile:

```sh
python patches/kai-v0.12.0-wifi-w1/tools/provision_wifi.py --port /dev/ttyACM0 clear
```

Clearing the Wi-Fi profile does **not** erase the firmware's Relay PSK/token. Use USB recovery if BLE is unavailable, the setup window has expired, or the saved profile is invalid.

## Security and privacy notes

- BLE provisioning uses **Security 1 + a fresh PoP**. Avoid sharing photos or screen recordings containing an active PoP.
- Wi-Fi credentials are kept locally in the device's Preferences/NVS profile after a successful network check. Integrity checks are **not** storage encryption.
- Firmware TLS-PSK and device token must match your self-hosted Relay; never publish them or commit `secrets.h`.
- The firmware includes tests for BLE/QR page wiring; the actual phone application and radio flow require an independent physical acceptance test.
