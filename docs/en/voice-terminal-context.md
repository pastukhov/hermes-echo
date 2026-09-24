# Hermes Voice Terminal: project context

This page summarizes current implementation facts for contributors. Prefer the executable code and user guides when they disagree with historical plans or handoff notes.

## Goal

Build a push-to-talk M5Stack StickS3 voice terminal. The device captures audio, streams it to Voice Gateway, and plays the returned response. The gateway runs STT, one configured agent (Hermes by default or optional Codex), and TTS. An optional local Codex adapter keeps Codex authentication on the host.

## Current sources of truth

- `docs/architecture.md`, `docs/protocol.md`, and `docs/development.md` describe current system behavior.
- `firmware/` contains the StickS3 port and host-side tests.
- `/home/artem/repos/esp-claw/application/rover_s3` is a reference implementation, not part of this repository.
- `docs/superpowers/`, handoff notes, and the original requirements document are historical and may be stale.

## StickS3 hardware

- ESP32-S3-PICO-1-N8R8, 8 MB flash and 8 MB Octal PSRAM.
- ES8311 mono codec, MEMS microphone, AW8737 amplifier.
- I2S: MCLK GPIO18, BCLK GPIO17, LRCK GPIO15, DOUT GPIO14, DIN GPIO16.
- Codec/PMU I2C: SDA GPIO47, SCL GPIO48.
- KEY1 GPIO11, active low; KEY2 GPIO12 is outside the MVP.
- Board-specific code is in `firmware/src/board_sticks3.c`; common state handling remains separate.

## Audio and protocol

- Capture: mono PCM S16LE at 16 kHz, streamed while KEY1 is held.
- V1 uses one synchronous `POST /api/v1/voice/turn` request.
- V2 uses a durable job, stable request UUID, status polling, and streamed WAV download. It requires a device-specific bearer token.
- Playback validates WAV metadata and streams through the ES8311 path; the v2 voice response is mono 24 kHz PCM16 WAV.
- A device ID is derived from the full Wi-Fi MAC. It is an identifier, not a credential.

## Wi-Fi setup and display

The open setup AP uses `Hermes-StickS3-Setup-XX`, with `XX` equal to the final MAC byte in hexadecimal. If a saved network does not obtain an IP within 60 seconds, setup AP starts while reconnection attempts continue. It closes when the device connects. The setup page is restricted to the setup subnet.

The display uses Montserrat-based glyphs. Voice states include READY, LISTENING, THINKING, SPEAKING, and ERROR; the error screen remains until a new button press. There is no separate LED status indicator.

## Server and Codex adapter

Voice Gateway is a FastAPI service. It archives turn artifacts under `ARCHIVE_ROOT`; v2 jobs are tracked in SQLite. `/health/live`, `/health/ready`, and `/metrics` are operational endpoints.

The host-side adapter runs as the signed-in user and listens on `127.0.0.1:8765`. The gateway authenticates to it with a dedicated adapter token. The adapter uses the pinned Python SDK and standard Codex runtime; do not copy credentials into Docker or firmware. Device tokens and adapter tokens serve different purposes and must not be reused.

## Verification boundaries

Backend and adapter unit tests use fakes and mocks; native firmware tests run without the physical device. They do not prove provider availability, Codex account authentication, target-board compilation, successful flashing, or audible playback. Check each of those separately before making a deployment or hardware-readiness claim.
