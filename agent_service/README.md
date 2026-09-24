# Codex Voice Agent

This host-side service gives each StickS3 device a durable Codex conversation. The Voice Gateway calls it over loopback; the firmware and Docker containers never receive Codex credentials.

## Runtime

- Python 3.12 in the local `.venv` with dependencies pinned in `requirements.txt`.
- `openai-codex` and its bundled Codex runtime are pinned to `0.156.1`.
- The service listens on `127.0.0.1:8765`; do not bind it to a LAN address.
- Runtime SQLite state defaults to `~/.local/state/hermes-echo/codex-voice/agent.sqlite3` and is created with restrictive permissions.
- Codex account credentials remain in the standard Codex home. Never copy `auth.json`, credential tokens, or the Codex home into a container or the device.

## Configuration

The systemd user unit reads a mode-0600 environment file at `~/.config/hermes-echo/codex-voice-agent.env`. Set `CODEX_AGENT_TOKEN` to a separately generated bearer secret. Optional settings are `CODEX_VOICE_CWD`, `CODEX_VOICE_MODEL`, and `CODEX_VOICE_TURN_TIMEOUT` (maximum 120 seconds). The gateway receives its own matching adapter token through its local environment; do not reuse the device token.

The adapter keeps one active conversation thread per `device_id`. Request IDs are idempotent; a repeated request with different transcript text is rejected. Reset creates a new active thread while retaining old SQLite history. An interrupted request is not replayed automatically after restart.

## Local checks

Run the fake-runtime tests without account requests:

```sh
.venv/bin/python -m pytest -q tests
```

The one-request SDK smoke makes a real, billable/quota-consuming Codex request and writes only non-secret diagnostics to the ignored `smoke-result.json`:

```sh
.venv/bin/python -m codex_voice.smoke
```

Check `GET /health/live` for process liveness and `GET /health/ready` for SDK/model readiness. Readiness checks account/runtime availability, not STT/TTS or device audio. Keep the gateway provider set to Hermes until an explicit end-to-end switch and device test are performed.
