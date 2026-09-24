# Codex voice adapter deployment

The Python SDK adapter runs on the host under the signed-in desktop user. It
does not mount or copy Codex credentials into Docker. The gateway reaches the
adapter through the existing host-network configuration at `127.0.0.1:8765`;
the systemd unit binds only to loopback.

Create a random adapter token and configure the same value in the host user
service env file and the gateway's local `.env`:

```sh
mkdir -p ~/.config/hermes-echo
install -m 600 deploy/codex-voice-agent.env.example \
  ~/.config/hermes-echo/codex-voice-agent.env
```

Replace the example token before starting anything. Configure
`VOICE_AGENT_PROVIDER=codex`, `CODEX_AGENT_URL=http://127.0.0.1:8765`, and the
matching `CODEX_AGENT_TOKEN` in the gateway environment. For protocol v2, set
`VOICE_DEVICE_TOKENS` to a JSON object that maps each configured device ID to
its own bearer token. Do not reuse the adapter token as a device token.

Install and start the user unit after Codex CLI is signed in as the service
user:

```sh
systemctl --user daemon-reload
systemctl --user enable --now codex-voice-agent.service
curl --fail http://127.0.0.1:8765/health/live
curl --fail http://127.0.0.1:8765/health/ready
```

The unit's working tree path assumes this repository is at
`~/hermes-echo`; edit the unit if it lives elsewhere. Readiness reports auth,
model, or runtime startup failures without exposing credentials. The gateway
continues to default to Hermes until `VOICE_AGENT_PROVIDER=codex` is explicit.
