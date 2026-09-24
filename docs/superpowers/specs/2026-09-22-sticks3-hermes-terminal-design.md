# StickS3 Hermes Voice Terminal Design

## Intent

Deliver a usable StickS3 voice terminal against the existing Hermes Voice
Gateway. The first milestone is a reliable local diagnostic image; the second
is the complete push-to-talk HTTP voice turn.

## Architecture

The firmware will use ESP-IDF through PlatformIO, with a thin board layer for
M5PM1, ST7789, buttons and ES8311. A voice application layer owns the state
machine and the HTTP transaction. Audio capture is bounded and streamed as raw
S16LE PCM; the response WAV is parsed incrementally and written to the codec in
bounded chunks. No full-turn audio buffer is required.

The backend remains the system of record for STT, Hermes reasoning, notes and
TTS. The device only knows the HTTP contract and does not embed model/provider
logic.

## States and behavior

`BOOT → IDLE → RECORDING → PROCESSING → PLAYING → IDLE`.

- IDLE shows a quiet ready screen.
- KEY1 press enters RECORDING; release or the configured maximum duration ends
  capture.
- RECORDING opens a chunked request and streams PCM while the button is held;
  PROCESSING sends the terminal body chunk, then reads and validates the HTTP
  response on the same connection. `finish` terminates the request body; it does
  not close the socket before the response is consumed.
- PLAYING accepts RIFF/WAVE PCM headers, configures the codec rate, streams the
  `data` chunk, then returns to IDLE at true playback drain.
- Transport errors, invalid status codes and malformed WAV responses enter ERROR
  and recover to IDLE without reboot.

## HTTP contract

`POST /api/v1/voice/turn` uses a chunked body with `Content-Type: audio/L16`,
`X-Sample-Rate: 16000`, `X-Channels: 1`, `X-Sample-Format: s16le`,
`X-Device-Id`, `X-Protocol-Version: 1`, and `Authorization: Bearer <device
token>` when configured. A successful response is `200 audio/wav`; error
responses are JSON and must never be sent to the WAV parser.

## Implementation boundaries

- `firmware/src/board_sticks3.c`: board peripherals and codec handles.
- `firmware/src/http_voice_client.c`: Wi-Fi HTTP request/response and streaming
  callbacks.
- `firmware/src/wav_parser.c`: incremental RIFF/fmt/data parser.
- `firmware/src/main.c`: application state transitions only.
- `firmware/test/`: parser, state, transport and failure-path tests.
- `docs/`: operator/build/flash instructions and evidence.

## Acceptance criteria

1. Backend imports cleanly and a configured full turn returns a non-empty valid
   WAV body; disabled stages have an explicit documented response.
2. `platformio run -e sticks3` succeeds for ESP32-S3.
3. `platformio test -e native` passes with no hardware dependency.
4. A diagnostic image boots, draws state UI, reads KEY1 and plays a local test
   tone or loopback sample.
5. A configured device can complete one authenticated chunked HTTP voice turn.
6. Invalid WAV, HTTP 4xx/5xx, disconnect and playback EOF all recover to IDLE.
7. A physical smoke test is recorded in `docs/` with firmware version, serial
   output and observed behavior.
