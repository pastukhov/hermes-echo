"""Tests for per-client / per-route rate limiting (task t_89295105).

Covers all four acceptance criteria:

1. per-client tracking (``RateLimiter.check`` + HTTP 429 on the client cap);
2. per-route tracking (route window exhausts independently of clients);
3. ``429`` + ``Retry-After`` header on rejection;
4. limits configurable through :class:`SecurityConfig` / env.

Unit tests hit :class:`RateLimiter` directly; HTTP tests drive a minimal
FastAPI app through ``starlette.testclient.TestClient`` (no network, no
gateway ``create_app`` — that path is blocked at this base by the missing
``health`` module, a pre-existing out-of-scope issue).
"""
from __future__ import annotations

import time
from unittest import mock

from fastapi import FastAPI
from starlette.testclient import TestClient

from backend.src.voice_gateway.config import (
    DEFAULT_RATE_LIMIT,
    DEFAULT_RATE_PERIOD,
    SecurityConfig,
)
from backend.src.voice_gateway.middleware import (
    PUBLIC_PATHS,
    RateLimiter,
    RateLimitMiddleware,
)


def _app(limiter: RateLimiter, limit: int, period: int) -> TestClient:
    fastapi_app = FastAPI()

    @fastapi_app.get("/a")
    def a():
        return {"route": "a"}

    @fastapi_app.get("/b")
    def b():
        return {"route": "b"}

    @fastapi_app.get("/health")
    def health():
        return {"status": "ok"}

    fastapi_app.add_middleware(
        RateLimitMiddleware, config=SecurityConfig(api_key="k", rate_limit=limit, rate_period=period),
        limiter=limiter,
    )
    return TestClient(fastapi_app)


# --- 1. per-client tracking (unit) -----------------------------------------


def test_per_client_limit_exceeded_returns_client():
    limiter = RateLimiter(rate_limit=2, rate_period=60)
    assert limiter.check("c1", "/a") == "ok"
    assert limiter.check("c1", "/a") == "ok"
    assert limiter.check("c1", "/a") == "client"


def test_per_client_isolated_between_clients():
    limiter = RateLimiter(rate_limit=1, rate_period=60)
    assert limiter.check("c1", "/a") == "ok"
    # c2 has its own window and is not affected by c1's traffic.
    assert limiter.check("c2", "/a") == "ok"
    assert limiter.check("c1", "/a") == "client"


def test_client_window_slides_forward():
    limiter = RateLimiter(rate_limit=1, rate_period=2)
    assert limiter.check("c1", "/a") == "ok"
    with mock.patch("time.time", return_value=time.time() + 3.0):
        assert limiter.check("c1", "/a") == "ok"


# --- 2. per-route tracking (unit) ------------------------------------------


def test_per_route_limit_exceeded_returns_route():
    limiter = RateLimiter(rate_limit=2, rate_period=60)
    # Two different clients, same route: client windows never fill, the
    # route window does.
    assert limiter.check("c1", "/a") == "ok"
    assert limiter.check("c2", "/a") == "ok"
    assert limiter.check("c3", "/a") == "route"


def test_route_windows_independent():
    limiter = RateLimiter(rate_limit=1, rate_period=60)
    assert limiter.check("c1", "/a") == "ok"
    # /b has its own route window.
    assert limiter.check("c1", "/b") == "ok"


# --- 3. HTTP: 429 + Retry-After --------------------------------------------


def test_http_429_with_retry_after_on_client_limit():
    limiter = RateLimiter(rate_limit=2, rate_period=45)
    client = _app(limiter, limit=2, period=45)
    for _ in range(2):
        resp = client.get("/a", headers={"X-Forwarded-For": "1.1.1.1"})
        assert resp.status_code == 200
    resp = client.get("/a", headers={"X-Forwarded-For": "1.1.1.1"})
    assert resp.status_code == 429
    assert resp.headers["Retry-After"] == "45"
    body = resp.json()
    assert body["error"] == "rate_limit_exceeded"
    assert body["retry_after"] == 45


def test_http_429_does_not_record_rejected_request():
    limiter = RateLimiter(rate_limit=1, rate_period=60)
    client = _app(limiter, limit=1, period=60)
    assert client.get("/a", headers={"X-Forwarded-For": "9.9.9.9"}).status_code == 200
    # Rejected requests must not push the client's window further out.
    for _ in range(5):
        resp = client.get("/a", headers={"X-Forwarded-For": "9.9.9.9"})
        assert resp.status_code == 429
        assert resp.headers["Retry-After"] == "60"


def test_http_per_route_limit_across_clients():
    limiter = RateLimiter(rate_limit=2, rate_period=60)
    client = _app(limiter, limit=2, period=60)
    assert client.get("/a", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 200
    assert client.get("/a", headers={"X-Forwarded-For": "2.2.2.2"}).status_code == 200
    # Route window full; a fresh client is rejected too.
    resp = client.get("/a", headers={"X-Forwarded-For": "3.3.3.3"})
    assert resp.status_code == 429
    # Other routes are unaffected.
    assert client.get("/b", headers={"X-Forwarded-For": "3.3.3.3"}).status_code == 200


def test_http_public_paths_exempt():
    limiter = RateLimiter(rate_limit=1, rate_period=60)
    client = _app(limiter, limit=1, period=60)
    assert "/health" in PUBLIC_PATHS
    for _ in range(5):
        assert client.get("/health").status_code == 200


# --- 4. configuration via SecurityConfig ------------------------------------


def test_security_config_defaults():
    cfg = SecurityConfig(api_key="k")
    assert cfg.rate_limit == DEFAULT_RATE_LIMIT
    assert cfg.rate_period == DEFAULT_RATE_PERIOD
    assert cfg.auth_enabled is True


def test_security_config_from_env_rate_fields():
    cfg = SecurityConfig.from_env({
        "VOICE_API_KEY": "k",
        "VOICE_RATE_LIMIT": "5",
        "VOICE_RATE_PERIOD": "30",
    })
    assert cfg.rate_limit == 5
    assert cfg.rate_period == 30


def test_security_config_from_env_rate_defaults():
    cfg = SecurityConfig.from_env({"VOICE_API_KEY": "k"})
    assert cfg.rate_limit == DEFAULT_RATE_LIMIT
    assert cfg.rate_period == DEFAULT_RATE_PERIOD


def test_middleware_disabled_passthrough():
    limiter = RateLimiter(rate_limit=1, rate_period=60)
    fastapi_app = FastAPI()

    @fastapi_app.get("/a")
    def a():
        return {"route": "a"}

    fastapi_app.add_middleware(
        RateLimitMiddleware,
        config=SecurityConfig(api_key="k", rate_limit=1, rate_period=60),
        limiter=limiter, enabled=False,
    )
    client = TestClient(fastapi_app)
    for _ in range(5):
        assert client.get("/a").status_code == 200
    assert len(limiter._client_windows) == 0
