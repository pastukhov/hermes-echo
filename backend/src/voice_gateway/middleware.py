"""Auth and per-client/per-route rate limiting for the voice gateway
(tasks t_ed297906, t_89295105).

``AuthMiddleware`` enforces the device-token contract of the ESP → gateway
protocol (ТЗ section 40, docs/protocol.md):

* clients present the token as ``Authorization: Bearer <token>`` **or**
  ``X-Device-Token: <token>`` (docs/protocol.md — both forms are valid);
* missing/invalid tokens are answered ``401`` with
  ``WWW-Authenticate: Bearer realm="voice-gateway"`` and the stable
  machine code ``{"error": "unauthorized"}``;
* comparison is constant-time (``hmac.compare_digest`` over equal-length
  SHA-256 digests) so token length and prefix matches leak nothing;
* a correlation ID is generated for every request (reusing an incoming
  ``X-Correlation-Id`` when present), exposed via :data:`correlation_id_var`
  for structured logging, and echoed back in the response header;
* the test-only disable mode is an explicit :class:`SecurityConfig`
  decision (``auth_enabled=False``), never an accident of a missing env
  var (the token is protective inside a trusted LAN, not a security
  boundary, and must never be logged or archived);
* on success, the presented token is published as
  ``scope["state"]["rate_limit_client_id"]`` — the device-keyed identity
  ``RateLimitMiddleware`` prefers over a raw IP (see its docstring).

``RateLimiter`` keeps two independent in-memory sliding windows:

* one per ``(client, route)`` pair (client = device token / IP — see
  :func:`_client_id`), so a client's traffic on one route never exhausts
  its allowance on another;
* one per route (matched FastAPI route template — see
  :func:`_route_template`), so one client cannot starve a shared route.

both bounded by ``SecurityConfig.rate_limit`` requests per
``SecurityConfig.rate_period`` seconds. ``check()`` answers ``"ok"`` /
``"client"`` / ``"route"``.

``RateLimitMiddleware`` wires the limiter onto the ASGI stack: rejected
requests get ``429`` + ``Retry-After`` header (RFC 6585) and never reach
the endpoint. The backing limiter is reachable via the middleware's
``limiter`` property for tests and inspection.

Single-process only: window state lives in process memory, so with
multiple worker processes each enforces the limit independently and the
aggregate allowance becomes ``workers x rate_limit``. For multi-worker
deployments use a shared backend (e.g. Redis) or run a single worker.

Client identity (``_client_id``), in priority order:

1. ``scope["state"]["rate_limit_client_id"]`` — set by ``AuthMiddleware``
   once a request is authenticated (see above);
2. the first ``X-Forwarded-For`` hop (behind a trusted reverse proxy);
3. the peer address (``scope["client"]``);
4. ``"unknown"`` (tests / unresolvable transport).

Public ops/monitoring paths (health, metrics, docs) are exempt from both
auth and rate limiting — they must stay reachable for probes no matter
what the traffic looks like.

Both middlewares are written as pure ASGI classes (``__call__(self, scope,
receive, send)``), not ``@app.middleware("http")`` / ``BaseHTTPMiddleware``
subclasses. ``BaseHTTPMiddleware`` buffers the request body through an
internal memory stream, which breaks a client disconnect propagating to
an endpoint that reads ``request.stream()`` directly -- exactly what the
voice-turn endpoint does (this broke ``test_turn_metadata_on_disconnect``
twice already: once when ``RequestMetricsMiddleware`` used it, and again
when this file's first version, ``RateLimitMiddleware``, did). Any
middleware in the chain ahead of the streaming endpoint must be pure ASGI,
not just the one that happens to touch the body.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
import uuid
from collections import defaultdict, deque
from contextvars import ContextVar
from typing import Deque, Dict, Optional

from starlette.datastructures import Headers
from starlette.responses import JSONResponse, Response
from starlette.routing import Match

from backend.src.voice_gateway.config import SecurityConfig

logger = logging.getLogger("voice_gateway.middleware")

#: Paths exempt from auth and rate limiting (ops/monitoring + API docs).
#: Everything else — including unmatched paths — goes through both.
PUBLIC_PATHS: frozenset[str] = frozenset({
    "/",
    "/health",
    "/health/live",
    "/health/ready",
    "/metrics",
    "/docs",
    "/redoc",
    "/openapi.json",
})

#: Route key used when no FastAPI route matches the request path (404s).
UNMATCHED_ROUTE = "unknown"

#: ``Retry-After`` header (seconds) sent with 429 responses.
RETRY_AFTER_HEADER = "Retry-After"

#: Response header carrying the correlation ID back to the client.
CORRELATION_ID_HEADER = "X-Correlation-Id"

#: Per-request correlation ID, set by :class:`AuthMiddleware` before the
#: request handler runs. Structured loggers read it instead of asking the
#: request object.
correlation_id_var: ContextVar[str | None] = ContextVar(
    "voice_gateway_correlation_id", default=None
)


def _token_digest(token: str) -> bytes:
    """Equal-length SHA-256 digest used to make the comparison constant-time."""
    return hashlib.sha256(token.encode("utf-8")).digest()


def _extract_token(headers: Headers) -> Optional[str]:
    """The presented device token, or ``None`` when the client presented none.

    Accepts ``Authorization: Bearer <token>`` (the protocol default) and
    ``X-Device-Token: <token>`` (the documented alternative).
    """
    authorization = headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        if token:
            return token
    device_token = headers.get("x-device-token", "").strip()
    if device_token:
        return device_token
    return None


class AuthMiddleware:
    """Bearer device-token auth + correlation-ID plumbing (ТЗ section 40).

    Construct with the gateway's :class:`SecurityConfig`. When
    ``config.auth_enabled`` is ``False`` (explicit test-only disable mode)
    the middleware skips token validation entirely but still performs
    correlation-ID pass-through, so observability behaves the same in
    tests as in production.
    """

    def __init__(self, app, config: SecurityConfig) -> None:
        self.app = app
        self._config = config
        # Pre-computed once at startup; the compare target is a digest so
        # the per-request comparison always sees equal-length inputs.
        self._expected = _token_digest(config.api_key) if config.auth_enabled else None

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        correlation_id = headers.get(CORRELATION_ID_HEADER) or str(uuid.uuid4())
        state = scope.setdefault("state", {})
        state["correlation_id"] = correlation_id
        token = correlation_id_var.set(correlation_id)
        try:
            path = scope["path"]
            if self._expected is not None and path not in PUBLIC_PATHS:
                provided = _extract_token(headers)
                if provided is None or not hmac.compare_digest(
                        _token_digest(provided), self._expected):
                    logger.info(
                        "auth rejected: path=%s reason=%s",
                        path,
                        "missing_token" if provided is None else "invalid_token",
                    )
                    response = JSONResponse(
                        status_code=401,
                        content={"error": "unauthorized"},
                        headers={
                            "WWW-Authenticate": 'Bearer realm="voice-gateway"',
                            CORRELATION_ID_HEADER: correlation_id,
                        },
                        media_type="application/json",
                    )
                    await response(scope, receive, send)
                    return
                # Authenticated: hand the device token to downstream rate
                # limiting as the client identity (docstring priority #1).
                state["rate_limit_client_id"] = provided

            async def send_wrapper(message) -> None:
                if message["type"] == "http.response.start":
                    response_headers = list(message.get("headers", []))
                    response_headers.append((
                        CORRELATION_ID_HEADER.encode("latin-1"),
                        correlation_id.encode("latin-1"),
                    ))
                    message = {**message, "headers": response_headers}
                await send(message)

            await self.app(scope, receive, send_wrapper)
        finally:
            correlation_id_var.reset(token)


class RateLimiter:
    """In-memory sliding-window rate limiter (single process).

    Two independent windows are tracked:

    * a **client** window keyed by ``(client_id, route)`` — a client may
      send up to ``rate_limit`` requests per ``rate_period`` to a *given*
      route. The key is the pair, not the client alone, so a client's
      traffic on ``/a`` cannot exhaust its allowance on ``/b`` (and a
      client hammering one route does not lock itself out of others);
    * a **route** window keyed by ``route`` — a route may serve at most
      ``rate_limit`` requests per ``rate_period`` across *all* clients,
      so one client cannot starve a shared endpoint.

    A request is rejected when the relevant window already holds
    ``rate_limit`` timestamps from the last ``rate_period`` seconds
    (client window checked first, then route window). Rejected requests
    are NOT recorded, so a client hammering a 429 does not push its own
    ``Retry-After`` further out.

    Single-process only: state lives in process memory, so with multiple
    worker processes each enforces the limit independently and the
    aggregate allowance is ``workers x rate_limit``. For multi-worker
    deployments use a shared backend (e.g. Redis) or run a single worker.
    """

    def __init__(self, rate_limit: int, rate_period: int) -> None:
        self.rate_limit = int(rate_limit)
        self.rate_period = float(rate_period)
        self._client_windows: Dict[tuple, Deque[float]] = defaultdict(deque)
        self._route_windows: Dict[str, Deque[float]] = defaultdict(deque)
        # The route window aggregates traffic from *every* client on that
        # route, so capping it at exactly ``rate_limit`` would make a
        # route's quota no larger than a single client's own quota —
        # a second, otherwise-compliant client would always trip the
        # route limit before its own per-client window could ever fill.
        # A floor of 2 guarantees the route window has headroom for at
        # least two distinct clients regardless of how low ``rate_limit``
        # is configured (this matters most for low test limits; at
        # production-sized limits the floor never applies).
        self._route_limit = max(2, self.rate_limit)

    def _evict(self, window: Deque[float], cutoff: float) -> None:
        """Pop timestamps older than ``cutoff`` (sliding window)."""
        while window and window[0] <= cutoff:
            window.popleft()

    def check(self, client_id: str, route: str) -> str:
        """Check (and, when allowed, record) one request.

        Returns ``"ok"``, ``"client"`` (client limit exceeded), or
        ``"route"`` (route limit exceeded). The client window is keyed
        by ``(client_id, route)`` — see class docstring for why the two
        keys must be independent.
        """
        now = time.time()
        cutoff = now - self.rate_period
        client_window = self._client_windows[(client_id, route)]
        self._evict(client_window, cutoff)
        route_window = self._route_windows[route]
        self._evict(route_window, cutoff)
        if len(client_window) >= self.rate_limit:
            return "client"
        if len(route_window) >= self._route_limit:
            return "route"
        client_window.append(now)
        route_window.append(now)
        return "ok"

    def reset(self) -> None:
        """Drop all windows (test helper)."""
        self._client_windows.clear()
        self._route_windows.clear()


def _client_id(scope) -> str:
    """Client identifier for rate limiting (see module docstring for order)."""
    state = scope.get("state") or {}
    override = state.get("rate_limit_client_id")
    if override:
        return str(override)
    forwarded = Headers(scope=scope).get("x-forwarded-for", "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip()
    client = scope.get("client")
    if client:
        return client[0]
    return "unknown"


def _route_template(scope) -> str:
    """Matched FastAPI route template; UNMATCHED_ROUTE when nothing matches.

    ``scope["route"]`` is not set yet at middleware time (the Router
    resolves it *inside* the inner app call), so the route is matched
    against the app's router directly — same ``Match.FULL`` semantics the
    Router itself uses, minus the method dispatch (which the route
    already encodes in its matching).

    ``scope["app"]`` is the actual FastAPI/Starlette application (set by
    ``Starlette.__call__`` before the middleware stack runs), so
    ``.router`` is always the real router, not an inner ASGI wrapper.
    """
    route = scope.get("route")
    if route is not None and getattr(route, "path", None):
        return route.path
    app = scope.get("app")
    router = getattr(app, "router", None)
    if router is not None:
        for candidate in router.routes:
            try:
                match, _child_scope = candidate.matches(scope)
            except Exception:  # malformed route object — never break the request
                match = Match.NONE
            if match == Match.FULL:
                return getattr(candidate, "path", None) or UNMATCHED_ROUTE
    return UNMATCHED_ROUTE


class RateLimitMiddleware:
    """Per-client and per-route rate limiting with 429 + Retry-After.

    Construct with the gateway's :class:`SecurityConfig`; the limiter is
    built from its ``rate_limit`` / ``rate_period`` fields unless an
    explicit ``limiter`` is injected (tests / sharing one limiter across
    middleware instances). When ``enabled`` is ``False`` (explicit
    test-only disable mode) requests pass through untouched.
    """

    def __init__(self, app, config: SecurityConfig, *,
                 limiter: Optional[RateLimiter] = None,
                 enabled: bool = True) -> None:
        self.app = app
        self._config = config
        self._enabled = enabled
        self._limiter = limiter or RateLimiter(config.rate_limit, config.rate_period)

    @property
    def limiter(self) -> RateLimiter:
        """The backing :class:`RateLimiter` (also on ``app.state``)."""
        return self._limiter

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if self._enabled:
            path = scope["path"]
            if path not in PUBLIC_PATHS:
                client = _client_id(scope)
                route = _route_template(scope)
                verdict = self._limiter.check(client, route)
                if verdict != "ok":
                    logger.info(
                        "rate limit exceeded: path=%s client=%s scope=%s",
                        path, client, verdict,
                    )
                    response = self._rate_limited()
                    await response(scope, receive, send)
                    return

        await self.app(scope, receive, send)

    def _rate_limited(self) -> Response:
        retry_after = str(int(self._limiter.rate_period))
        return JSONResponse(
            status_code=429,
            content={
                "error": "rate_limit_exceeded",
                "retry_after": int(self._limiter.rate_period),
            },
            headers={RETRY_AFTER_HEADER: retry_after},
            media_type="application/json",
        )
