# Flashing StickS3

Build the ESP32-S3 image from `firmware/`:

```sh
/home/artem/platformio/.venv/bin/platformio run -d firmware -e sticks3
```

Hold KEY1 while connecting USB when download mode is required, then use
`platformio run -e sticks3 -t upload` and `platformio device monitor`.

The target is configured for 8 MB flash and Octal PSRAM. A first boot must log
the detected flash/PSRAM sizes before hardware audio is considered validated.
Do not put Wi-Fi credentials or device tokens in this file or in `platformio.ini`.

For a private build, pass credentials and endpoint as `HERMES_WIFI_SSID`,
`HERMES_WIFI_PASSWORD`, and `HERMES_GATEWAY_URL` environment variables. PlatformIO consumes these as build
flags; keep the values outside the repository. Settings can also be changed
from the device's setup page and are saved in NVS. Blank password/token fields
in the page keep the currently stored secret.

## Setup page

Without saved Wi-Fi credentials, connect to `Hermes-StickS3-Setup-XX`, where
`XX` is the final byte of the device's Wi-Fi MAC in lowercase hexadecimal.
With saved credentials, the device tries the home network first. If it has
not obtained an IP within one minute (also after a later disconnect), it
starts the setup AP while continuing to retry the home connection. Once it
gets an IP, the setup AP turns off. The phone should detect the
captive portal and offer to open the setup page. If it does not, open
`http://192.168.4.1/` manually.
The page scans nearby Wi-Fi networks and configures the SSID/password, voice
gateway URL, and optional device token. The device ID is always derived from
the Wi-Fi MAC and is shown on the screen. A successful save restarts
the device. The page and its JSON/scan endpoints reject clients outside the
device setup subnet, so they are not available through the home/station LAN.
The setup AP is open (no password), so use it only during local provisioning.

## On-device status display

The StickS3 screen uses a compact Russian status view: «ГОТОВ», «СЛУШАЮ»,
«ДУМАЮ», «ОТВЕЧАЮ», and «ОШИБКА» each have a distinct accent and central
indicator. The Wi-Fi bars reflect station connectivity; short Russian button
hints appear under the current state. The rendering is native RGB565 and uses
the existing ST7789 driver without an additional graphics-library dependency.
