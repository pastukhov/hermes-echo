"""Per-client / per-route rate limiting for the voice gateway (task t_89295105).

``RateLimiter`` keeps two independent in-memory sliding windows:

* one per ``(client, route)`` pair (client = device / API key / IP —
  see :func:`_client_id`), so a client's traffic on one route never
  exhausts its allowance on another;
* one per route (matched FastAPI route template — see
  :func:`_route_template`), so one client cannot starve a shared route.

both bounded by ``SecurityConfig.rate_limit`` requests per
``SecurityConfig.rate_period`` seconds (task t_89295105). ``check()``
answers ``"ok"`` / ``"client"`` / ``"route"``.

``RateLimitMiddleware`` wires the limiter onto the ASGI stack: rejected
requests get ``429`` + ``Retry-After`` header (RFC 6585) and never reach
the endpoint. The backing limiter is reachable via the middleware's
``limiter`` property for tests and inspection.

Single-process only: window state lives in process memory, so with
multiple worker processes each enforces the limit independently and the
aggregate allowance becomes ``workers x rate_limit``. For multi-worker
deployments use a shared backend (e.g. Redis) or run a single worker.

Client identity (``_client_id``), in priority order:

1. ``request.state.rate_limit_client_id`` — set by an upstream
   middleware (e.g. the device-token auth middleware, task t_ed297906)
   when the request is already authenticated; this is the API-key /
   device-keyed identity (ТЗ: "by IP or API key");
2. the first ``X-Forwarded-For`` hop (behind a trusted reverse proxy);
3. the peer address (``request.client.host``);
4. ``"unknown"`` (tests / unresolvable transport).

Public ops/monitoring paths (health, metrics, docs) are exempt — they
must stay reachable for probes no matter what the traffic looks like.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Match

from backend.src.voice_gateway.config import SecurityConfig

logger = logging.getLogger("voice_gateway.rate_limit")

#: Paths exempt from rate limiting (ops/monitoring + API docs).
#: Everything else — including unmatched paths — goes through the limiter.
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


def _client_id(request: Request) -> str:
    """Client identifier for rate limiting (see module docstring for order)."""
    override = getattr(request.state, "rate_limit_client_id", None)
    if override:
        return str(override)
    forwarded = request.headers.get("x-forwarded-for", "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client is not None:
        return request.client.host
    return "unknown"


def _route_template(request: Request, app) -> str:
    """Matched FastAPI route template; UNMATCHED_ROUTE when nothing matches.

    ``scope["route"]`` is not set yet at middleware time (the Router
    resolves it *inside* the call_next), so the route is matched against
    the app's router directly — same ``Match.FULL`` semantics the Router
    itself uses, minus the method dispatch (which the route already
    encodes in its matching).

    ``app`` must be the actual FastAPI/Starlette application (i.e.
    ``request.app``), not ``BaseHTTPMiddleware.app`` — the latter is the
    *inner* ASGI callable the middleware wraps (e.g.
    ``ExceptionMiddleware``), which has no ``.router`` and would make
    every request resolve to :data:`UNMATCHED_ROUTE`.
    """
    route = request.scope.get("route")
    if route is not None and getattr(route, "path", None):
        return route.path
    router = getattr(app, "router", None)
    if router is not None:
        for candidate in router.routes:
            try:
                match, _child_scope = candidate.matches(request.scope)
            except Exception:  # malformed route object — never break the request
                match = Match.NONE
            if match == Match.FULL:
                return getattr(candidate, "path", None) or UNMATCHED_ROUTE
    return UNMATCHED_ROUTE


class RateLimitMiddleware(BaseHTTPMiddleware):
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
        super().__init__(app)
        self._config = config
        self._enabled = enabled
        self._limiter = limiter or RateLimiter(config.rate_limit, config.rate_period)

    @property
    def limiter(self) -> RateLimiter:
        """The backing :class:`RateLimiter` (also on ``app.state``)."""
        return self._limiter

    async def dispatch(self, request: Request, call_next) -> Response:
        if self._enabled:
            path = request.scope["path"]
            if path not in PUBLIC_PATHS:
                client = _client_id(request)
                route = _route_template(request, request.app)
                verdict = self._limiter.check(client, route)
                if verdict != "ok":
                    logger.info(
                        "rate limit exceeded: path=%s client=%s scope=%s",
                        path, client, verdict,
                    )
                    return self._rate_limited()
        return await call_next(request)

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
