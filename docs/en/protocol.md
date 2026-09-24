# StickS3 ↔ Voice Gateway HTTP protocol

Firmware uses `X-Protocol-Version: 1` by default; v2 is enabled in the setup page. The current transport is HTTP. Keep both endpoints within a trusted network.

## V1: upload a voice turn

```http
POST /api/v1/voice/turn HTTP/1.1
Host: gateway:8080
Transfer-Encoding: chunked
Content-Type: audio/L16
X-Sample-Rate: 16000
X-Channels: 1
X-Sample-Format: s16le
X-Device-Id: a1b2c3d4e5f6
X-Protocol-Version: 1
Authorization: Bearer <device-token>
```

The current v1 gateway does not validate a device bearer token. Firmware may send `Authorization`, but that does not enable server-side v1 authentication. Use v1 only on a trusted LAN. For v2, the gateway validates the bearer token mapped to `X-Device-Id`. A MAC/device ID is an identifier, not a secret.

The body is raw PCM S16LE, 16,000 Hz, one channel, little-endian signed 16-bit samples. The ESP opens the POST when recording starts and streams chunks as they are captured. Releasing the button ends the chunked request. The full audio body must not be buffered in RAM on either the device or the backend.

| Header | Value |
| --- | --- |
| `Content-Type` | `audio/L16` |
| `X-Sample-Rate` | `16000` |
| `X-Channels` | `1` |
| `X-Sample-Format` | `s16le` |
| `X-Device-Id` | Stable StickS3 ID: full Wi-Fi MAC in hex, e.g. `a1b2c3d4e5f6` |
| `X-Protocol-Version` | `1` |
| `Authorization` | Firmware may send this header; current v1 gateway does not validate it |

`VOICE_API_KEY` in `.env.example` does not add authentication to `/api/v1/voice/turn`. Do not rely on it to protect the v1 endpoint.

## V1 response

```http
HTTP/1.1 200 OK
Content-Type: audio/wav
X-Turn-Id: 550e8400-e29b-41d4-a716-446655440000
```

When TTS is configured, the response contains mono PCM signed 16-bit WAV; the sample rate is read from the WAV header. Firmware validates the HTTP status, `Content-Type`, and WAV metadata, then streams audio to playback without buffering the full recording in RAM. Without TTS, v1 may return success with no audio; TTS is required for a spoken reply.

## V2: asynchronous voice turn

V2 adds a durable job API for replies that may take longer than one HTTP request. V1 remains available.

```http
POST /api/v2/voice/turns HTTP/1.1
Content-Type: audio/L16
X-Protocol-Version: 2
X-Request-Id: 9b69da5b-bd5d-44a3-9391-15e8dac36733
X-Device-Id: a1b2c3d4e5f6
X-Sample-Rate: 16000
X-Channels: 1
Authorization: Bearer <device-token>
```

The device creates one UUID per recording. The body is streamed mono 16 kHz PCM S16LE, up to 3,840,000 bytes. After the full upload is stored and queued, the server responds `202`:

```json
{"turn_id":"<uuid>","request_id":"<uuid>","status":"queued"}
```

If the `202` response is lost, do not upload with a new ID; check `GET /api/v2/voice/requests/{request_id}`. For an accepted turn, poll `GET /api/v2/voice/turns/{turn_id}` about once per second. Status progresses through `queued → transcribing → thinking → synthesizing → ready`; the processing states are shown on the device. When status is `ready`, download `GET /api/v2/voice/turns/{turn_id}/audio`. The file must be valid mono 24 kHz PCM S16LE WAV. Only a `200` response with `Content-Type: audio/wav` may be sent to audio playback.

`POST /api/v2/voice/turns/{turn_id}/cancel` cancels current work. `POST /api/v2/voice/sessions/reset` starts a new device conversation. Every request requires a bearer token mapped to that `X-Device-Id`; an inaccessible turn and an unknown turn both return `404`.

Repeating an upload with the same device ID, request ID, and SHA-256 returns the existing turn without running the agent again. A different body with the same ID returns `409 idempotency_conflict`; a parallel upload returns `409 upload_in_progress`; a full queue returns `429 agent_busy`. On restart, unfinished jobs (`running`, `transcribing`, `thinking`, or `synthesizing`) become `interrupted` and are not automatically retried.

`X-Turn-Id` links the response to its archive directory. A legacy-compatible handler may omit it; the current gateway returns it.

## Error responses

```http
HTTP/1.1 <4xx or 5xx>
Content-Type: application/json
X-Turn-Id: <uuid>
```

```json
{"error":"stt_failed","turn_id":"..."}
```

`error` is a machine-readable code; `turn_id` is the turn UUID if one has already been created. Responses do not include secrets, diagnostics, or binary audio. Firmware keeps the error on screen until the button is pressed.

| Code | HTTP | Meaning |
| --- | ---: | --- |
| `unauthorized` | 401 | V2 token does not match `X-Device-Id` |
| `protocol_version_required` | 400 | V2 upload lacks `X-Protocol-Version: 2` |
| `invalid_request_id` | 400 | V2 request ID is not a UUID |
| `audio_too_large` | 413 | V2 upload exceeds 3,840,000 bytes |
| `audio_invalid` | 400 | PCM upload is empty or has an odd byte count |
| `idempotency_conflict` | 409 | Same request ID was used with a different audio body |
| `upload_in_progress` | 409 | Another upload with this request ID is still running |
| `agent_busy` | 429 | V2 queue is full |
| `audio_not_ready` | 409 | WAV was requested before processing finished |
| `stt_failed`, `agent_unavailable`, `tts_failed` | terminal status | V2 processing failed; the code is in the status payload's `error` field |

V1 HTTP status and JSON shape depend on the failure stage. Firmware should not depend on arbitrary diagnostic text.

## Operational endpoints

```http
GET /health/live
GET /health/ready
GET /metrics
```

`/health/live` returns `200` when the process is alive. `/health/ready` returns `200` when configuration is loaded and the required archive is writable; otherwise it returns `503`. `/metrics` uses Prometheus exposition format and never uses transcripts, titles, turn IDs, or error text as labels.

## Internal Hermes contract

The Hermes provider sends the STT transcript to the OpenAI-compatible endpoint configured by `HERMES_BASE_URL`, `HERMES_API_KEY`, `HERMES_MODEL`, and `HERMES_TIMEOUT`. The expected response is:

```json
{
  "reply": "A short spoken reply.",
  "note": {"create": true, "title": "...", "content": "...", "tags": ["voice"]}
}
```

The `note` field is archived with the agent response. Although the repository includes an Obsidian `NoteStore`, the active pipeline does not currently write notes to a vault. This JSON is an internal gateway contract, not an ESP payload.
