# Hermes Voice Terminal · StickS3

A push-to-talk voice terminal built around the M5Stack StickS3. Hold the button, speak, and release it; the device sends the recording to Voice Gateway and plays the reply.

Documentation: [English](docs/en/index.md) · [Русский](docs/ru/index.md).

The project contains device firmware, a Python gateway, and an optional host-side Codex service. Hermes is the default agent. Codex can be enabled explicitly. Neither the firmware nor the Docker container receives Codex credentials.

![System components and security boundaries](docs/assets/system-overview.svg)

## Quick start

You need Docker Compose and reachable STT, agent (Hermes or the local Codex Agent), and TTS endpoints.

```sh
cp .env.example .env
# Configure local endpoints and models in .env; keep secrets in this local file.
docker compose up --build -d backend
curl --fail http://127.0.0.1:8080/health/live
curl --fail http://127.0.0.1:8080/health/ready
```

Compose uses `network_mode: host`, allowing the gateway to reach host-local services. Check network isolation before running on a shared or untrusted network; never expose the gateway directly to the Internet. See the [development guide](docs/en/development.md) for other checks and non-Docker startup.

## Connect the StickS3

Without saved Wi-Fi settings, the device creates the open network `Hermes-StickS3-Setup-XX`, where `XX` is the final byte of the Wi-Fi MAC in hexadecimal. Your phone may offer to open the setup portal. If not, browse to `http://192.168.4.1/`.

In the setup form, choose a network and enter the gateway endpoint:

- **v1:** the full URL, for example `http://192.168.1.10:8080/api/v1/voice/turn`.
- **v2:** the base URL only, for example `http://192.168.1.10:8080`, plus a separate device token.

The saved network gets one minute to connect. If it cannot, the setup AP appears while the device continues retrying. The AP shuts down after a successful connection. For v2, the gateway must have a device ID-to-token mapping; see [device setup](docs/en/flash-sticks3.md) and the [protocol guide](docs/en/protocol.md).

## Voice request

The device follows a half-duplex cycle: `READY → LISTENING → THINKING → SPEAKING → READY`. An error remains on screen until you press the button. There is no separate LED indication.

With v1, one synchronous HTTP request handles the turn. With v2, the gateway first stores the recording as a job; the device then checks job status and downloads the WAV. V2 requires a separate bearer token for each device.

## Repository layout

| Path | Purpose |
| --- | --- |
| `firmware/` | ESP-IDF/PlatformIO firmware for M5Stack StickS3 |
| `backend/` | FastAPI gateway, Hermes/Codex orchestration, STT/TTS, archive, and v2 jobs |
| `agent_service/` | Local host-side Codex Python SDK adapter |
| `deploy/` | systemd unit and Codex adapter installation guide |
| `docs/` | Architecture, protocol, development, device setup, and diagrams |
| `docker-compose.yml` | Containerized gateway startup |
| `.env.example` | Environment-variable template without working secrets |

## Security and data

- The backend stores voice recordings and results in the archive. Review its location, access controls, and retention policy before using personal recordings.
- The Codex provider and protocol v2 are opt-in. Firmware defaults to v1; the gateway defaults to Hermes.
- The setup AP is open and intended only for local provisioning.
- Never commit `.env`, Wi-Fi passwords, device tokens, or Codex credentials.
