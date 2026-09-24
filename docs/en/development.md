# Develop and run the project

Run commands from the repository root unless a section says otherwise. The project contains a Python gateway, a separate Python Codex adapter, and ESP-IDF firmware for the device.

## Requirements

- Python 3.12.
- Docker Compose for the optional containerized gateway.
- PlatformIO Core and the ESP-IDF environment configured by `firmware/platformio.ini` to build firmware.
- Reachable OpenAI-compatible STT and TTS endpoints. Hermes is required when `VOICE_AGENT_PROVIDER=hermes`; the host adapter is required for `codex`.

## Configure the environment

```sh
cp .env.example .env
# Set local endpoint URLs, model names, and secrets in .env; never commit it.
```

The minimum configuration for a voice turn is `STT_BASE_URL`, `TTS_BASE_URL`, `TTS_MODEL`, and one agent provider. For Hermes, set `HERMES_BASE_URL` and optionally its key/model. For Codex, set `VOICE_AGENT_PROVIDER=codex`, `CODEX_AGENT_URL`, and `CODEX_AGENT_TOKEN`. See `.env.example` and `docker-compose.yml` for variable names and defaults.

## Start the gateway

### With Docker Compose

```sh
docker compose up --build -d backend
curl --fail http://127.0.0.1:8080/health/live
curl --fail http://127.0.0.1:8080/health/ready
curl --fail http://127.0.0.1:8080/metrics
```

Compose runs the gateway in host network mode. This lets it reach host-local services such as Hermes on `127.0.0.1`; however, a port bound to `0.0.0.0` may also be reachable by other devices on the network. Check your firewall and LAN trust. Stop it with `docker compose down`.

### Locally

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r backend/requirements.txt -r agent_service/requirements.txt
uvicorn backend.src.voice_gateway.app:app --host 127.0.0.1 --port 8080
```

To run the Codex adapter separately, follow the [deployment guide](../../deploy/codex-voice-agent.md).

## Run checks

Run backend tests from the repository root; the root `conftest.py` configures the import path:

```sh
pytest -q backend
cd agent_service
python -m pytest -q tests
```

These tests use fake runtimes and mocked HTTP transports. They do not prove that the current Codex session is authenticated or that external STT/TTS endpoints are reachable.

Firmware native tests do not need a connected device:

```sh
cd firmware
pio test -e native
```

Build for the target board:

```sh
cd firmware
export HERMES_WIFI_SSID='your-network'
export HERMES_WIFI_PASSWORD='your-password'
export HERMES_GATEWAY_URL='http://192.168.1.10:8080/api/v1/voice/turn'
pio run -e sticks3
```

Build flags read the Wi-Fi and gateway values from the environment and embed defaults in the image. Firmware also stores portal settings in NVS. Do not put real values in shell history, Git, or documentation; use a protected local environment file for repeatable builds. See the [StickS3 guide](flash-sticks3.md) for flashing.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `/health/live` works but `/health/ready` returns 503 | The `checks` field for required STT/agent configuration and write access to `ARCHIVE_ROOT` |
| The device does not appear on the home network | After one minute, look for `Hermes-StickS3-Setup-XX`; station reconnection continues in the background |
| Wi-Fi works but a voice turn fails | Gateway URL, protocol version, device/token mapping, STT/TTS settings, and gateway logs |
| V2 upload was accepted but there is no reply | Poll `GET /api/v2/voice/turns/{turn_id}` and inspect archive metadata; terminal failures appear in the `error` status payload |
| Codex service is not ready | Sign in to Codex CLI as the same system user and check the adapter's `GET /health/ready`; do not copy credentials into the container |

## Data and security

- The archive contains audio, transcripts, and replies. Restrict access and define a retention period.
- Unless `VOICE_JOB_DATABASE` is overridden, the v2 job database is stored alongside the archive.
- `/health/ready` does not test current external STT/TTS network availability.
- Do not use real recordings or keys in tests. The open setup AP is for local provisioning only.
- Before exposing the gateway, check its bind address, firewall, and logs for secrets.
