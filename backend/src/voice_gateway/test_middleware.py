"""Tests for auth + per-client/per-route rate limiting (tasks t_ed297906,
t_89295105).

Rate-limit section covers all four of t_89295105's acceptance criteria:

1. per-client tracking (``RateLimiter.check`` + HTTP 429 on the client cap);
2. per-route tracking (route window exhausts independently of clients);
3. ``429`` + ``Retry-After`` header on rejection;
4. limits configurable through :class:`SecurityConfig` / env.

Auth section covers t_ed297906's acceptance criteria:

* missing token → 401 + ``WWW-Authenticate``
* valid token (Bearer or X-Device-Token) → request reaches the handler
* explicit test-disable mode → auth skipped, handler reached
* correlation IDs generated, reused when incoming, exposed to handlers
* timing-safe comparison (``hmac.compare_digest``) actually used

Rate-limit HTTP tests drive a minimal FastAPI app through
``starlette.testclient.TestClient``; auth tests use ``httpx``'s
``ASGITransport`` directly (same pattern as ``test_app_stream.py``) since
they also assert on request-scoped state (``request.state.correlation_id``)
that a probe endpoint reads back. Both exercise the same underlying ASGI
middleware protocol, so either style works for either middleware — the
split here just follows what each task's tests were originally written
against.
"""
from __future__ import annotations

import asyncio
import time
from unittest import mock

import httpx
import pytest
from fastapi import FastAPI, Request
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from backend.src.voice_gateway.config import (
    DEFAULT_RATE_LIMIT,
    DEFAULT_RATE_PERIOD,
    SecurityConfig,
    SecurityConfigError,
)
from backend.src.voice_gateway.middleware import (
    CORRELATION_ID_HEADER,
    PUBLIC_PATHS,
    AuthMiddleware,
    RateLimiter,
    RateLimitMiddleware,
    correlation_id_var,
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


# ---------------------------------------------------------------------------
# AuthMiddleware (task t_ed297906)
# ---------------------------------------------------------------------------

AUTH_API_KEY = "test-device-token-42"


def _auth_app(config: SecurityConfig) -> FastAPI:
    """Bare FastAPI app with AuthMiddleware + a probe endpoint.

    The probe reports what it saw so tests can assert on the correlation
    ID pass-through and on auth outcomes without a real gateway endpoint.
    """
    app = FastAPI()
    app.add_middleware(AuthMiddleware, config=config)

    @app.get("/probe")
    async def probe(request: Request):
        return {
            "state_correlation_id": request.state.correlation_id,
            "ctxvar_correlation_id": correlation_id_var.get(),
        }

    @app.get("/health/live")
    async def health_live():
        return {"status": "ok"}

    @app.get("/metrics")
    async def metrics():
        return JSONResponse(
            content="# metric\n", media_type="text/plain; version=0.0.4"
        )

    return app


def _auth_client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                             base_url="http://test")


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _authorized() -> SecurityConfig:
    return SecurityConfig(api_key=AUTH_API_KEY, auth_enabled=True)


class TestMissingTokenRejected:
    def test_no_token_returns_401_with_www_authenticate(self):
        app = _auth_app(_authorized())

        async def go():
            async with _auth_client(app) as client:
                response = await client.get("/probe")
                assert response.status_code == 401
                assert "Bearer" in response.headers["WWW-Authenticate"]
                assert response.json() == {"error": "unauthorized"}
                assert response.headers[CORRELATION_ID_HEADER]

        _run(go())

    def test_bare_bearer_without_value_is_rejected(self):
        app = _auth_app(_authorized())

        async def go():
            async with _auth_client(app) as client:
                response = await client.get(
                    "/probe", headers={"Authorization": "Bearer"})
                assert response.status_code == 401
                assert "Bearer" in response.headers["WWW-Authenticate"]

        _run(go())

    def test_wrong_token_returns_401(self):
        app = _auth_app(_authorized())

        async def go():
            async with _auth_client(app) as client:
                response = await client.get(
                    "/probe", headers={"Authorization": "Bearer wrong-token"})
                assert response.status_code == 401
                assert "Bearer" in response.headers["WWW-Authenticate"]
                assert response.json() == {"error": "unauthorized"}

        _run(go())

    def test_non_bearer_authorization_scheme_falls_back_to_device_token(self):
        # A non-Bearer scheme is NOT a token: with no X-Device-Token either,
        # the request is rejected, not treated as a token candidate.
        app = _auth_app(_authorized())

        async def go():
            async with _auth_client(app) as client:
                response = await client.get(
                    "/probe", headers={"Authorization": "Basic abc"})
                assert response.status_code == 401

        _run(go())


class TestValidTokenAccepted:
    def test_valid_bearer_token_reaches_handler(self):
        app = _auth_app(_authorized())

        async def go():
            async with _auth_client(app) as client:
                response = await client.get(
                    "/probe",
                    headers={"Authorization": f"Bearer {AUTH_API_KEY}"})
                assert response.status_code == 200
                assert response.json()["state_correlation_id"]

        _run(go())

    def test_x_device_token_alternative_is_accepted(self):
        # docs/protocol.md: `X-Device-Token: <token>` is the documented
        # alternative to `Authorization: Bearer <token>`.
        app = _auth_app(_authorized())

        async def go():
            async with _auth_client(app) as client:
                response = await client.get(
                    "/probe", headers={"X-Device-Token": AUTH_API_KEY})
                assert response.status_code == 200

        _run(go())

    def test_public_paths_do_not_require_a_token(self):
        # Ops/monitoring endpoints stay token-free so probes keep working
        # even when auth is enabled (docs/protocol.md service endpoints).
        app = _auth_app(_authorized())

        async def go():
            async with _auth_client(app) as client:
                for path in ("/health/live", "/metrics"):
                    response = await client.get(path)
                    assert response.status_code == 200, path

        _run(go())


class TestDisableMode:
    def test_disabled_auth_skips_validation_but_still_works(self):
        config = SecurityConfig(api_key=AUTH_API_KEY, auth_enabled=False)
        app = _auth_app(config)

        async def go():
            async with _auth_client(app) as client:
                # No token, wrong token — both pass through untouched.
                response = await client.get("/probe")
                assert response.status_code == 200
                response = await client.get(
                    "/probe", headers={"Authorization": "Bearer nope"})
                assert response.status_code == 200

        _run(go())

    def test_disabled_mode_still_generates_correlation_ids(self):
        config = SecurityConfig(api_key="", auth_enabled=False)
        app = _auth_app(config)

        async def go():
            async with _auth_client(app) as client:
                response = await client.get("/probe")
                assert response.status_code == 200
                assert response.headers[CORRELATION_ID_HEADER]

        _run(go())


class TestCorrelationIds:
    def test_correlation_id_generated_and_exposed(self):
        app = _auth_app(_authorized())

        async def go():
            async with _auth_client(app) as client:
                response = await client.get(
                    "/probe", headers={"Authorization": f"Bearer {AUTH_API_KEY}"})
                header = response.headers[CORRELATION_ID_HEADER]
                body = response.json()
                # Handler saw the same ID via request.state AND the
                # ContextVar (the logging integration point).
                assert body["state_correlation_id"] == header
                assert body["ctxvar_correlation_id"] == header

        _run(go())

    def test_incoming_correlation_id_is_reused(self):
        app = _auth_app(_authorized())

        async def go():
            async with _auth_client(app) as client:
                response = await client.get(
                    "/probe",
                    headers={"Authorization": f"Bearer {AUTH_API_KEY}",
                            CORRELATION_ID_HEADER: "trace-abc-123"})
                assert response.headers[CORRELATION_ID_HEADER] == "trace-abc-123"
                assert response.json()["state_correlation_id"] == "trace-abc-123"

        _run(go())

    def test_correlation_id_generated_even_on_401(self):
        # Observability must not depend on auth success.
        app = _auth_app(_authorized())

        async def go():
            async with _auth_client(app) as client:
                response = await client.get("/probe")
                assert response.status_code == 401
                assert response.headers[CORRELATION_ID_HEADER]

        _run(go())


class TestTimingSafeComparison:
    def test_uses_hmac_compare_digest(self, monkeypatch):
        calls = []
        real = __import__("hmac").compare_digest

        def spy(a, b):
            calls.append((a, b))
            return real(a, b)

        import hmac
        monkeypatch.setattr(hmac, "compare_digest", spy)
        app = _auth_app(_authorized())

        async def go():
            async with _auth_client(app) as client:
                await client.get(
                    "/probe", headers={"Authorization": f"Bearer {AUTH_API_KEY}"})
                await client.get(
                    "/probe", headers={"Authorization": "Bearer wrong"})

        _run(go())
        assert calls, "hmac.compare_digest was not used"
        # Both inputs are equal-length digests (no length oracle): the raw
        # client token never reaches the comparison.
        for a, b in calls:
            assert len(a) == len(b) == 32  # SHA-256 digest size


class TestSecurityConfigAuthFromEnv:
    def test_key_implies_auth_enabled(self):
        config = SecurityConfig.from_env({"VOICE_API_KEY": "sekret"})
        assert config.api_key == "sekret"
        assert config.auth_enabled is True

    def test_no_key_no_override_disables_auth(self):
        config = SecurityConfig.from_env({})
        assert config.auth_enabled is False
        assert config.api_key == ""

    def test_explicit_false_is_test_disable_mode_even_with_key(self):
        config = SecurityConfig.from_env(
            {"VOICE_API_KEY": "sekret", "VOICE_AUTH_ENABLED": "false"})
        assert config.auth_enabled is False

    def test_explicit_true_with_key(self):
        config = SecurityConfig.from_env(
            {"VOICE_API_KEY": "sekret", "VOICE_AUTH_ENABLED": "true"})
        assert config.auth_enabled is True

    def test_explicit_true_without_key_raises(self):
        with pytest.raises(SecurityConfigError, match="VOICE_API_KEY"):
            SecurityConfig.from_env({"VOICE_AUTH_ENABLED": "true"})

    def test_garbage_override_raises(self):
        with pytest.raises(SecurityConfigError, match="VOICE_AUTH_ENABLED"):
            SecurityConfig.from_env({"VOICE_AUTH_ENABLED": "banana"})

    def test_config_is_frozen(self):
        config = SecurityConfig.from_env({"VOICE_API_KEY": "sekret"})
        with pytest.raises(AttributeError):
            config.api_key = "other"  # pyright: ignore[reportAttributeAccessIssue]
