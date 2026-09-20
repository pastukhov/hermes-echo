"""Prometheus metrics for the voice gateway (ТЗ section 34).

The MVP exposes the minimal metric set from ТЗ §34 on ``GET /metrics``:

    voice_turns_total{status}
    voice_turn_duration_seconds
    voice_audio_duration_seconds
    voice_stt_duration_seconds
    voice_hermes_duration_seconds
    voice_tts_duration_seconds
    voice_notes_total{status}
    voice_active_turns
    request_count{client_id, route, status}
    request_latency_seconds{endpoint, status}
    model_request_count{model_name, status}
    model_request_latency_seconds{model_name}
    healthy_models{model_name}
    rate_limit_events{client_id, route, status}
    active_requests
    request_count_by_route{route}

Label discipline (ТЗ §34): never put transcript, title, turn_id, error
messages or arbitrary text in labels — only bounded enums: the ``status``
enum (``success`` / stable ``ErrorCode`` values) and the ``endpoint`` route
name (a fixed set of registered routes) in ``request_latency_seconds``.

Every metric set is created per app instance (``init_metrics``) so tests can
use isolated registries and concurrent app instances never share counters.
"""
from __future__ import annotations

from typing import Iterable, Mapping

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
)

SERVICE = "voice-gateway"

#: Histogram buckets for per-stage durations (seconds): 50 ms … 60 s.
_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60)

#: Histogram buckets for HTTP request latencies (seconds): 10 ms … 60 s.
#: Used by both REQUEST_LATENCY and MODEL_REQUEST_LATENCY (ТЗ §34).
_REQUEST_BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60)


class VoiceMetrics:
    """One metric namespace for a single app instance."""

    def __init__(self, registry: CollectorRegistry) -> None:
        self.registry = registry
        self.turns_total = Counter(
            "voice_turns_total",
            "Voice turns by terminal status.",
            ("status",),
            registry=registry,
        )
        self.notes_total = Counter(
            "voice_notes_total",
            "Notes written to the note storage by terminal status.",
            ("status",),
            registry=registry,
        )
        # REQUEST_COUNT: middleware counter, incremented once per request by
        # the request middleware (ТЗ §34). Labels are deliberately low-cardinality:
        # ``client_id`` (bounded device identifier), ``route`` (a fixed set of
        # endpoint paths), and ``status`` (``success`` / stable ``ErrorCode``
        # values). Label discipline (ТЗ §34) still applies — no arbitrary text
        # in any of the three labels.
        self.request_count = Counter(
            "request_count",
            "Total number of requests per client_id, route, and status.",
            ("client_id", "route", "status"),
            registry=registry,
        )
        # REQUEST_LATENCY: per-request HTTP latency histogram (ТЗ §34).
        # ``endpoint`` is a bounded set of endpoint paths, ``status`` the
        # bounded status enum — no arbitrary text in labels.
        self.request_latency = Histogram(
            "request_latency_seconds",
            "HTTP request latency per endpoint and status.",
            ("endpoint", "status"),
            registry=registry,
            buckets=_REQUEST_BUCKETS,
        )
        # MODEL_REQUEST_COUNT: counter of model (LLM) requests per model and
        # status (ТЗ §34). ``model_name`` is a bounded catalog value.
        self.model_request_count = Counter(
            "model_request_count",
            "Model requests by model name and status.",
            ("model_name", "status"),
            registry=registry,
        )
        # MODEL_REQUEST_LATENCY: model request latency per model (ТЗ §34).
        self.model_request_latency = Histogram(
            "model_request_latency_seconds",
            "Model request latency per model name.",
            ("model_name",),
            registry=registry,
            buckets=_REQUEST_BUCKETS,
        )
        # HEALTHY_MODELS_GAUGE: 1 = healthy, 0 = not healthy (ТЗ §34).
        self.healthy_models = Gauge(
            "healthy_models",
            "Model health (1 = healthy, 0 = not healthy).",
            ("model_name",),
            registry=registry,
        )
        # RATE_LIMIT_EVENTS: rate limiter firings per client, route, status
        # (ТЗ §34). Same bounded-label discipline as REQUEST_COUNT.
        self.rate_limit_events = Counter(
            "rate_limit_events",
            "Rate limit events by client_id, route, and status.",
            ("client_id", "route", "status"),
            registry=registry,
        )
        # ACTIVE_REQUESTS: current in-flight request concurrency, no labels
        # (ТЗ §34) — label-free gauges must be unique per registry.
        self.active_requests = Gauge(
            "active_requests",
            "Requests currently being processed.",
            registry=registry,
        )
        # REQUEST_COUNT_BY_ROUTE: request volume per route only (ТЗ §34).
        # ``route`` is a fixed set of endpoint paths — bounded.
        self.request_count_by_route = Counter(
            "request_count_by_route",
            "Total number of requests per route.",
            ("route",),
            registry=registry,
        )
        self.turn_duration = Histogram(
            "voice_turn_duration_seconds",
            "End-to-end voice turn duration.",
            registry=registry,
            buckets=_BUCKETS,
        )
        self.audio_duration = Histogram(
            "voice_audio_duration_seconds",
            "Duration of the recorded audio per turn.",
            registry=registry,
            buckets=_BUCKETS,
        )
        self.stt_duration = Histogram(
            "voice_stt_duration_seconds",
            "STT stage duration.",
            registry=registry,
            buckets=_BUCKETS,
        )
        self.hermes_duration = Histogram(
            "voice_hermes_duration_seconds",
            "Hermes (LLM) stage duration.",
            registry=registry,
            buckets=_BUCKETS,
        )
        self.tts_duration = Histogram(
            "voice_tts_duration_seconds",
            "TTS stage duration.",
            registry=registry,
            buckets=_BUCKETS,
        )
        self.active_turns = Gauge(
            "voice_active_turns",
            "Voice turns currently being processed.",
            registry=registry,
        )


#: Process-wide namespace used by the module-level ``app`` (production).
_DEFAULT: VoiceMetrics | None = None


def get_metrics() -> VoiceMetrics:
    """Return the process-wide metric namespace (created on first use)."""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = VoiceMetrics(CollectorRegistry())
    return _DEFAULT


def init_metrics(registry: CollectorRegistry | None = None) -> VoiceMetrics:
    """Create a fresh namespace on ``registry`` (or the default one)."""
    return VoiceMetrics(registry if registry is not None else CollectorRegistry())
