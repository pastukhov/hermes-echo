"""Voice Gateway application for durable asynchronous device voice turns."""
from __future__ import annotations

import os
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response
from prometheus_client import generate_latest
from starlette.datastructures import Headers

from backend.src.voice_gateway.agents.base import AgentClient
from backend.src.voice_gateway.agents.codex_client import CodexAgentClient
from backend.src.voice_gateway.jobs.api import install_voice_job_routes
from backend.src.voice_gateway.jobs.auth import parse_device_tokens
from backend.src.voice_gateway.jobs.store import VoiceJobStore
from backend.src.voice_gateway.jobs.worker import VoiceJobWorker
from backend.src.voice_gateway.health import check_live, check_ready
from backend.src.voice_gateway.config import (
    AgentConfig,
    AgentConfigError,
    HermesConfig,
    HermesConfigError,
    SecurityConfig,
    STTConfig,
    STTConfigError,
    load_hermes_prompt,
)
from backend.src.voice_gateway.logging_config import configure_logging
from backend.src.voice_gateway.middleware import (
    AuthMiddleware,
    RateLimiter,
    RateLimitMiddleware,
)
from backend.src.voice_gateway.hermes.base import HermesClient
from backend.src.voice_gateway.hermes.client import OpenAICompatibleHermesClient
from backend.src.voice_gateway.hermes.stage import HermesStage
from backend.src.voice_gateway.metrics import init_metrics
from backend.src.voice_gateway.pipeline import VoicePipeline
from backend.src.voice_gateway.stt.base import STTProvider
from backend.src.voice_gateway.stt.client import OpenAICompatibleSTT
from backend.src.voice_gateway.tts.base import TTSProvider
from backend.src.voice_gateway.tts.config import TTSConfig
from backend.src.voice_gateway.tts.openai_compatible import OpenAICompatibleTTS

class RequestMetricsMiddleware:
    """Record the four request-level metrics for every request (ТЗ §34).

    Written as a pure ASGI middleware (not ``@app.middleware("http")`` /
    ``BaseHTTPMiddleware``) because ``BaseHTTPMiddleware`` buffers the
    request body through an internal stream, which breaks a client
    disconnect propagating to an endpoint that reads ``request.stream()``
    directly (a known Starlette limitation) — exactly what the voice-turn
    endpoint does. This class wraps ``scope``/``receive``/``send``
    unmodified instead, so streaming and disconnects behave the same as
    with no middleware installed at all.

    The route label is the matched path *template*
    (``scope["route"].path``, a fixed finite set) — never the raw URL
    path, which would be unbounded cardinality (see metrics.py's
    label-discipline note). Unmatched paths (404s, no route resolved) use
    the literal "unmatched" label instead. The ``/metrics`` scrape
    endpoint itself passes through this same middleware like any other
    request — it is not special-cased.
    """

    def __init__(self, app, metrics) -> None:
        self.app = app
        self.metrics = metrics

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        self.metrics.active_requests.inc()
        start = time.perf_counter()
        status = "500"

        async def send_wrapper(message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = str(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            matched_route = scope.get("route")
            route = matched_route.path if matched_route is not None else "unmatched"
            client_id = Headers(scope=scope).get("X-Device-Id", "unknown")
            elapsed = time.perf_counter() - start
            self.metrics.active_requests.dec()
            self.metrics.request_latency.labels(endpoint=route, status=status).observe(elapsed)
            self.metrics.request_count.labels(
                client_id=client_id, route=route, status=status
            ).inc()
            self.metrics.request_count_by_route.labels(route=route).inc()

def _default_stt_provider() -> STTProvider | None:
    """Build the production STTProvider from environment config (ТЗ §20).

    Returns ``None`` when STT is not configured (``STT_BASE_URL`` unset) so
    the caller can fall back to this milestone's ingest-only contract — the
    same behavior as before STT was wired in. Only ever consulted when the
    caller did not explicitly inject a provider (tests always pass one
    explicitly, so this path only matters for the production singleton).
    """
    try:
        config = STTConfig.from_env()
    except STTConfigError:
        return None
    return OpenAICompatibleSTT(config)


def _default_hermes_client() -> HermesClient | None:
    try:
        config = HermesConfig.from_env()
        prompt = load_hermes_prompt()
    except HermesConfigError:
        return None
    return OpenAICompatibleHermesClient(config, system_prompt=prompt)


def _default_agent_client() -> AgentClient | None:
    try:
        config = AgentConfig.from_env()
    except AgentConfigError:
        return None
    if config.provider == "codex":
        try:
            return CodexAgentClient(config.codex_url, config.codex_token)
        except ValueError:
            return None
    return None


def _default_tts_provider() -> TTSProvider | None:
    try:
        config = TTSConfig.from_env(os.environ)
    except ValueError:
        return None
    return OpenAICompatibleTTS(config)


def create_app(
    archive_root: str | os.PathLike | None = None,
    stt: STTProvider | None = None,
    hermes: HermesClient | None = None,
    tts: TTSProvider | None = None,
    agent: AgentClient | None = None,
    security: SecurityConfig | None = None,
) -> FastAPI:
    """Build the gateway app.

    ``archive_root`` defaults to ``$ARCHIVE_ROOT`` or ``./archive`` (tests
    pass a tmp dir). ``stt`` / ``hermes`` are optional provider
    dependencies injected by the caller (tests pass fakes explicitly). If
    a provider is not injected, the app builds it from environment config;
    unconfigured stages remain disabled. When STT is unavailable the app
    still boots and a plain audio
    turn still succeeds with 200 + X-Turn-Id — this milestone's ingest-only
    contract ("backend может ответить простым 200 OK без аудио-тела [без
    STT/Hermes/TTS]") is preserved.
    """
    # Structured JSON logging (ТЗ §33): idempotent, safe to call every time
    # create_app() runs (e.g. once per test in this session).
    configure_logging()

    root = Path(os.environ.get("ARCHIVE_ROOT", "archive")) if archive_root is None \
        else Path(archive_root)

    # One metric namespace per app instance (ТЗ §34) so tests can use
    # isolated registries and concurrent apps never share counters.
    metrics = init_metrics()

    # Explicit provider injection wins; production providers come from config.
    stt_provider = stt if stt is not None else _default_stt_provider()

    # Hermes stage: explicit dependencies win; production uses environment
    # config. The endpoint wraps the client once, per app instance; concurrent
    # turns share the stage safely because the raw payload travels through
    # a task-local ContextVar, not an instance attribute.
    try:
        agent_config = AgentConfig.from_env()
        provider = agent_config.provider
    except AgentConfigError:
        agent_config = None
        provider = os.environ.get("VOICE_AGENT_PROVIDER", "hermes").strip().lower()
    if agent is not None:
        agent_client = agent
        hermes_client = None
        provider = "codex"
    elif agent_config is not None and agent_config.provider == "codex":
        agent_client = _default_agent_client()
        hermes_client = None
    elif agent_config is not None and agent_config.provider == "hermes":
        agent_client = None
        hermes_client = hermes if hermes is not None else _default_hermes_client()
    else:
        agent_client = None
        hermes_client = None
    tts_provider = tts if tts is not None else _default_tts_provider()
    hermes_stage = HermesStage(hermes_client) if hermes_client is not None else None

    job_database = os.environ.get("VOICE_JOB_DATABASE", str(root / "voice-jobs.sqlite"))
    job_store = VoiceJobStore(job_database, root)
    pipeline = VoicePipeline(stt_provider, agent_client, hermes_stage, tts_provider)
    job_worker = VoiceJobWorker(job_store, pipeline.run)
    try:
        device_tokens = parse_device_tokens(os.environ.get("VOICE_DEVICE_TOKENS"))
    except ValueError:
        device_tokens = {}

    app = FastAPI(title="Hermes Voice Gateway", version="0.2.0")
    app.add_middleware(RequestMetricsMiddleware, metrics=metrics)
    app.state.stt_provider = stt_provider
    app.state.hermes_client = hermes_client
    app.state.agent_client = agent_client
    app.state.agent_provider = provider
    app.state.tts_provider = tts_provider
    app.state.voice_job_store = job_store
    app.state.voice_job_worker = job_worker

    async def reset_device(device_id: str):
        if agent_client is None or not hasattr(agent_client, "reset"):
            raise HTTPException(status_code=503, detail={"error": "agent_reset_unavailable"})
        await agent_client.reset(device_id)

    install_voice_job_routes(
        app, job_store, job_worker, device_tokens, reset_device=reset_device
    )

    @app.on_event("startup")
    async def start_voice_jobs() -> None:
        await job_worker.start()

    @app.on_event("shutdown")
    async def close_agent_client() -> None:
        await job_worker.close()
        if agent_client is not None:
            close = getattr(agent_client, "close", None)
            if close is not None:
                await close()

    @app.get("/health")
    async def health() -> dict:
        """Liveness/readiness probe (ТЗ §15.1)."""
        return {
            "status": "ok",
            "service": "voice-gateway",
            "version": app.version,
        }

    @app.get("/health/live")
    async def health_live() -> dict:
        """Liveness probe (ТЗ §35): process alive, nothing else."""
        return check_live()

    @app.get("/health/ready")
    async def health_ready() -> JSONResponse:
        """Readiness probe (ТЗ §35): config loaded + archive writable.

        Readiness must not depend on the momentary availability of
        Hermes/STT/TTS (ТЗ §35) — no network calls here, ever, so a
        hung provider cannot create a restart loop. The notes vault is
        probed and reported but never blocking (writable/optional, ТЗ
        §25).
        """
        report = check_ready(
            archive_root=root,
            note_root=os.environ.get("OBSIDIAN_VAULT_PATH"),
            env=os.environ,
        )
        if provider == "codex" and agent_client is None:
            payload = report.as_dict()
            payload["status"] = "not_ready"
            payload["checks"]["config"] = (
                "error:CODEX_AGENT_URL or CODEX_AGENT_TOKEN is invalid"
            )
            return JSONResponse(status_code=503, content=payload)
        return JSONResponse(
            status_code=200 if report.ready else 503,
            content=report.as_dict(),
        )

    @app.get("/metrics")
    async def metrics_endpoint() -> Response:
        """Prometheus scrape endpoint (ТЗ §34)."""
        payload = generate_latest(metrics.registry)
        return Response(content=payload, media_type="text/plain; version=0.0.4")

    security = security if security is not None else SecurityConfig.from_env()
    rate_limiter = RateLimiter(security.rate_limit, security.rate_period)
    app.state.rate_limiter = rate_limiter
    app.add_middleware(
        RateLimitMiddleware, config=security, limiter=rate_limiter
    )
    app.add_middleware(AuthMiddleware, config=security)

    return app


app = create_app()
