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
    request_latency_seconds{endpoint,status}

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
        self.request_latency = Histogram(
            "request_latency_seconds",
            "Per-request processing duration.",
            ("endpoint", "status"),
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
