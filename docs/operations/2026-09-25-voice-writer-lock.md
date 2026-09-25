# Voice recorder error on button release

The device request at 2026-09-25 18:10 Moscow time reached the gateway but
returned HTTP 502 with `agent_unavailable`. Codex rejected resuming the
persisted conversation with `thread ... already has an active writer`.
The desktop Codex app-server (PID 7660 during diagnosis) held both the
conversation file and its writer lock while the task was idle. Stopping
the voice agent did not release that lock; a new independent Codex turn
worked. This was live ownership by another process, not a stale lock file.

The user explicitly approved resetting the recorder session and fixing the
cause. The authenticated session reset endpoint reset device `7ce8b1e4b780`;
the previous conversation remains in session history. No lock files or
authentication files were removed or changed.

The voice runtime now reuses conversations it already owns. On the exact
SDK writer-conflict error during resume, it forks the conversation history
with the same model, working directory, read-only sandbox and deny-all
approval policy. The service persists the returned conversation ID for
subsequent requests and restarts. Other resume failures do not trigger a
fork. This preserves the desktop conversation and avoids competing writers.
A fork is a snapshot; subsequent desktop messages are not synchronized back
into the recorder conversation.

Validation: 22 agent-service tests passed, including writer conflict,
permission preservation, unrelated errors, and persistence across restart.
A live recovery from the original desktop-owned conversation created a fork
and returned a successful model response. The voice service was restarted
to load the fix. Physical button/speaker verification remains a user check.

After restart, two sequential replays of the failed device recording through
`POST /api/v1/voice/turn` using the real device ID both returned HTTP 200
and valid mono 24 kHz WAV responses (67,244 and 93,644 bytes, respectively).
This verifies upload, STT, Codex, TTS and continued conversation server-side;
it does not verify playback through the physical speaker.
