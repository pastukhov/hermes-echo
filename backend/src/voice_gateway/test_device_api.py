"""Integration coverage for the device-only voice job API."""

from __future__ import annotations

import uuid
import json

from fastapi.testclient import TestClient

from backend.src.voice_gateway.app import create_app
from backend.src.voice_gateway.config import SecurityConfig


DEVICE_ID = "001122334455"
DEVICE_TOKEN = "device-token-for-test"
GLOBAL_TOKEN = "separate-global-token"


def _headers(**overrides: str) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {DEVICE_TOKEN}",
        "X-Device-Id": DEVICE_ID,

        "X-Request-Id": str(uuid.uuid4()),
    }
    headers.update(overrides)
    return headers


def test_device_token_auth_is_independent_of_global_api_key(tmp_path, monkeypatch):
    """A valid registered device token must not also equal VOICE_API_KEY."""
    monkeypatch.setenv("VOICE_DEVICE_TOKENS", json.dumps({DEVICE_ID: DEVICE_TOKEN}))
    app = create_app(
        archive_root=tmp_path / "archive",
        security=SecurityConfig(api_key=GLOBAL_TOKEN, auth_enabled=True),
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v2/voice/turns", content=b"\x00\x00", headers=_headers()
        )

    assert response.status_code == 202
    assert response.headers["x-correlation-id"]
    assert response.json()["status"] == "queued"


def test_device_routes_reject_invalid_credentials_without_leaking_identity(tmp_path, monkeypatch):
    """Every device route rejects absent, wrong, unknown, and foreign credentials."""
    other_id = "66778899aabb"
    monkeypatch.setenv(
        "VOICE_DEVICE_TOKENS",
        json.dumps({DEVICE_ID: DEVICE_TOKEN, other_id: "other-device-token"}),
    )
    app = create_app(
        archive_root=tmp_path / "archive",
        security=SecurityConfig(api_key=GLOBAL_TOKEN, auth_enabled=True),
    )
    turn_id = str(uuid.uuid4())
    request_id = str(uuid.uuid4())
    routes = [
        ("post", "/api/v2/voice/turns"),
        ("get", f"/api/v2/voice/requests/{request_id}"),
        ("get", f"/api/v2/voice/turns/{turn_id}"),
        ("get", f"/api/v2/voice/turns/{turn_id}/audio"),
        ("post", f"/api/v2/voice/turns/{turn_id}/cancel"),
        ("post", "/api/v2/voice/sessions/reset"),
    ]
    bad_credentials = [
        _headers(**{"X-Device-Id": ""}),
        _headers(Authorization=""),
        _headers(Authorization="Bearer wrong-token"),
        _headers(**{"X-Device-Id": "unknown-device"}),
        _headers(**{"X-Device-Id": DEVICE_ID, "Authorization": "Bearer other-device-token"}),
    ]

    with TestClient(app) as client:
        for method, path in routes:
            for headers in bad_credentials:
                response = client.request(
                    method.upper(), path, content=b"\x00\x00", headers=headers
                )
                assert response.status_code == 401
                assert response.headers["x-correlation-id"]
                assert DEVICE_ID not in response.text
                assert DEVICE_TOKEN not in response.text


def test_synchronous_v1_voice_route_is_not_registered(tmp_path):
    """The gateway exposes only the durable asynchronous device API."""
    app = create_app(
        archive_root=tmp_path / "archive",
        security=SecurityConfig(api_key="", auth_enabled=False),
    )

    with TestClient(app) as client:
        response = client.post("/api/v1/voice/turn", content=b"\x00\x00")

    assert all(getattr(route, "path", None) != "/api/v1/voice/turn" for route in app.routes)
    assert response.status_code == 404
