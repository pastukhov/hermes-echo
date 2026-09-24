# StickS3 Hermes Voice Terminal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the incomplete ATOM-era firmware with a buildable, testable StickS3 Hermes voice terminal.

**Architecture:** Keep the backend HTTP contract and isolate board-specific code behind a small hardware seam. Port the proven StickS3 display/PMU/ES8311 patterns from `rover_s3` and `cap_voice`, then add a bounded HTTP/WAV path.

**Tech Stack:** ESP-IDF 5.5.3 via PlatformIO 6.13 platform, C, Unity native tests, FastAPI backend, M5GFX/esp_codec_dev.

**Spec:** `docs/superpowers/specs/2026-09-22-sticks3-hermes-terminal-design.md`

## Global Constraints

- Target is M5Stack StickS3 (ESP32-S3-PICO-1-N8R8), never ATOM Echo.
- Never commit Wi-Fi passwords, device tokens, provider keys or auth files.
- Keep audio bounded and stream PCM/WAV; do not require a full-turn RAM buffer.
- Preserve existing user changes in the dirty checkout.
- Every production behavior change starts with a failing test.

## Review Focus

- A truncated or extra RIFF chunk must fail safely and return to IDLE.
- HTTP JSON errors must never be interpreted as audio.
- I2S capture/playback rate changes must not leave the shared codec locked.
- A Wi-Fi disconnect during upload or playback must release all handles.
- A battery-powered speaker path must cap output volume and keep the UI responsive.
- Chunked upload must define ownership, backpressure, short writes, overflow and bounded work per tick.
- The backend integration gate must prove a non-empty valid WAV before firmware consumes the response.
- The ESP32 bootstrap must name `app_main`, pin native dependencies and compile both firmware environments.
- The transport proof must exercise the selected ESP-IDF `esp_http_client` against Uvicorn, including unknown-length PCM and same-socket response reads.
- Queue ownership and state completion must be explicit: no accepted PCM may be dropped, and PLAYING ends only after transport EOF, parser finish, and DMA drain.

### Task 1: Freeze and document the project

**Files:**
- Modify: `docs/voice-terminal-context.md`
- Create: `docs/superpowers/specs/2026-09-22-sticks3-hermes-terminal-design.md`
- Create: this plan
- Modify: `/home/artem/.hermes/config.yaml` to disable Kanban dispatch

Verify the dispatcher is disabled and no task worker is running. Keep the SQLite
board untouched except for stopping active execution if one exists; the global
freeze was already applied before this plan was written.

### Task 2: Repair and lock the backend voice contract

**Files:**
- Modify: `backend/src/voice_gateway/app.py:257`
- Modify: `backend/src/voice_gateway/app.py` success response paths
- Modify: `backend/src/voice_gateway/test_app_stream.py`
- Modify: `docs/protocol.md`

Write a failing import test and a full-turn test first. Fix the conditional
syntax error, then make configured STT → Hermes → TTS return a non-empty WAV.
Make no-provider behavior explicit in tests and documentation. Add tests for the
exact `audio/L16` headers and JSON error responses. Do not start firmware
integration until the backend test proves a valid WAV response.

### Task 3: Prove the chunked transport seam

**Files:**
- Create: `firmware/include/voice_transport.h`
- Create: `firmware/src/voice_transport.c`
- Create: `firmware/test/test_voice_transport/test_voice_transport.c`
- Modify: `backend/src/voice_gateway/test_app_stream.py`

Define `voice_transport_begin`, `voice_transport_write`, `voice_transport_finish`
and `voice_transport_poll` with explicit `accepted_bytes`, `WOULD_BLOCK`, fatal
error and ownership rules. Use a fake transport to prove a request opens before
recording, accepts bounded chunks, terminates once, and preserves the response
connection. Then run an on-target/integration transport test using the selected
ESP-IDF `esp_http_client` against a local Uvicorn endpoint: the request has
unknown-length PCM, terminates the body exactly once, and reads the response on
the same connection. `finish` terminates the HTTP body; it must not close the
socket before the response is consumed. Test slow readers, short writes, zero
progress, 401 and disconnects.

### Task 4: Make the ESP32-S3 build target explicit

**Files:**
- Modify: `firmware/platformio.ini`
- Modify: `firmware/sdkconfig.defaults`
- Modify: `firmware/src/CMakeLists.txt`
- Create/modify: `firmware/include/board_sticks3.h`

Add an `sticks3` environment using `esp32-s3-devkitc-1`, 8 MB flash/PSRAM and
the pinned ESP-IDF platform. Declare the managed-component manifest with pinned
`esp_codec_dev` and display-library versions, provide `app_main`, and either add
the C++ bridge needed by M5GFX or explicitly choose a C-only display driver.
Remove stale ATOM header references. Add a source allowlist so host-only files
cannot enter the target build. First add a build smoke test or compile gate that
fails on the old board name, run it red, then make both `platformio run -e
sticks3` and the native environment compile. Capture Flash/PSRAM detection in
the target serial diagnostic before declaring bootstrap complete.

### Task 5: Implement board, display and codec initialization

**Files:**
- Modify: `firmware/src/board_sticks3.c`
- Modify: `firmware/include/hardware.h`
- Create: `firmware/src/sticks3_display.c`
- Create: `firmware/include/sticks3_display.h`
- Test: `firmware/test/test_main/test_main.c` and native fakes

Port only verified GPIO/I2C/I2S values from `rover_s3`. Use one owned I2C bus,
`esp_codec_dev` for ES8311, M5PM1 for LCD/speaker power, and a simple state
renderer. The hardware seam returns errors and takes an explicit audio format;
capture stop → close codec → configure playback → drain → close playback →
restore 16 kHz capture is the required transition. Define queue owners,
capacities, overflow cleanup and bounded nonblocking worker/poll behavior. Add
tests for button polarity, state color mapping, idempotent audio start/stop,
rollback, starvation before EOF, partial writes without duplication, and
overflow cleanup. PLAYING may transition to IDLE only after transport EOF,
successful `wav_parser_finish`, no unaccepted PCM, and DMA drain.

Run the physical diagnostic gate immediately after this task: boot UI, KEY1,
short local capture/playback, speaker volume and repeated 16→24→16 kHz cycles.

### Task 6: Add incremental WAV parsing

**Files:**
- Create: `firmware/include/wav_parser.h`
- Create: `firmware/src/wav_parser.c`
- Create: `firmware/test/test_wav_parser/test_wav_parser.c`

Define `wav_parser_init`, `wav_parser_feed`, `wav_parser_finish` and a
`wav_audio_format_t` containing rate, channels, bits and data length. `feed`
returns consumed bytes and emits bounded PCM blocks to a sink that can report
`accepted_bytes` or `WOULD_BLOCK`. Support PCM=1 mono 16-bit at 16/24 kHz,
valid padded ancillary chunks, block-alignment and byte-rate checks. Test every
split position, one-byte feeds, truncation, missing data, oversized lengths,
odd padding, valid extra chunks and HTTP error bypass.

### Task 7: Add Wi-Fi and HTTP voice client

**Files:**
- Create: `firmware/include/http_voice_client.h`
- Create: `firmware/src/http_voice_client.c`
- Modify: `firmware/src/main.c`
- Test: native client/state tests with a transport seam

Expose the transport seam from Task 3 and add Wi-Fi readiness, bounded reconnect,
URL/device ID/token configuration, upload/read timeouts and offline UI state.
Open the chunked request in RECORDING, send the required `audio/L16` headers and
device token, stream PCM chunks while recording, then expose response chunks and
classify disconnects. Never automatically replay an indeterminate completed
turn; the next button press starts a fresh one.

### Task 8: Integrate PLAYING and recovery behavior

**Files:**
- Modify: `firmware/src/main.c`
- Modify: `firmware/include/hardware.h`
- Modify: `firmware/test/test_main/test_main.c`

Use the parser and client to implement PROCESSING/PLAYING, true EOF drain,
malformed-response recovery and cleanup. Add failing tests for each acceptance
path, then run native tests and the ESP32-S3 build.

### Task 9: Backend contract verification and operator docs

**Files:**
- Modify: `docs/protocol.md`
- Modify: `docs/development.md`
- Create: `docs/flash-sticks3.md`
- Modify: backend tests only if a contract gap is demonstrated

Document configuration without secrets, flashing/download mode, serial monitor,
gateway URL and a reproducible curl/backend smoke test. Run backend tests and
record all known physical limitations.

### Task 10: Physical smoke test

Use the real StickS3 to verify boot UI, KEY1, codec capture/playback, Wi-Fi,
authenticated request and WAV response. Run repeated turns, max-duration while
held, Wi-Fi loss during upload/download, recovery and memory/watchdog checks.
Record firmware hash, serial output and pass/fail observations in
`docs/smoke-test-sticks3.md`.
