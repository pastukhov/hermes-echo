"""Unit tests for the VoiceMetrics namespace (ТЗ §34).

Covers the ``request_latency_seconds`` histogram (endpoint/status labels)
and guarantees every metric from the MVP set is exposed through a fresh
registry — the shape the ``/metrics`` endpoint scrapes.
"""
from prometheus_client import CollectorRegistry, generate_latest


def _scrape_text(metrics) -> str:
    return generate_latest(metrics.registry).decode("utf-8")


def test_request_latency_histogram_defined_with_labels() -> None:
    from backend.src.voice_gateway.metrics import VoiceMetrics

    m = VoiceMetrics(CollectorRegistry())
    # Observe with the endpoint + status labels the card requires.
    m.request_latency.labels(
        endpoint="/api/v1/voice/turn", status="success").observe(0.42)
    m.request_latency.labels(
        endpoint="/api/v1/voice/turn", status="stt_failed").observe(0.07)

    text = _scrape_text(m)
    assert 'request_latency_seconds_count{endpoint="/api/v1/voice/turn",' \
           'status="success"} 1.0' in text
    assert 'request_latency_seconds_bucket{endpoint="/api/v1/voice/turn",' \
           'le="0.5",status="success"} 1.0' in text
    assert 'request_latency_seconds_count{endpoint="/api/v1/voice/turn",' \
           'status="stt_failed"} 1.0' in text


def test_request_latency_uses_request_buckets() -> None:
    """The histogram must reuse the shared ``_REQUEST_BUCKETS`` (also used by
    ``model_request_latency``), not the per-stage-duration ``_BUCKETS``."""
    from backend.src.voice_gateway import metrics as metrics_mod

    m = metrics_mod.VoiceMetrics(CollectorRegistry())
    m.request_latency.labels(endpoint="/api/v1/voice/turn",
                             status="success").observe(0.3)

    text = _scrape_text(m)
    # prometheus_client renders each boundary as a float: 1 -> "1.0",
    # 2.5 -> "2.5", 0.05 -> "0.05". Format the boundary the same way.
    for boundary in metrics_mod._REQUEST_BUCKETS:
        rendered = f"{float(boundary)}"
        line = 'request_latency_seconds_bucket{endpoint="/api/v1/voice/turn",' \
               f'le="{rendered}",status="success"}} '
        assert any(l.startswith(line) for l in text.splitlines()), \
            f"bucket {boundary} missing from scrape output"


def test_existing_metrics_not_broken() -> None:
    """The pre-existing metric set must still register and scrape."""
    from backend.src.voice_gateway.metrics import VoiceMetrics

    m = VoiceMetrics(CollectorRegistry())
    m.turns_total.labels(status="success").inc()
    m.notes_total.labels(status="success").inc()
    m.turn_duration.observe(1.2)
    m.audio_duration.observe(0.25)
    m.stt_duration.observe(0.3)
    m.hermes_duration.observe(0.6)
    m.tts_duration.observe(0.2)
    m.active_turns.inc()

    text = _scrape_text(m)
    for name in (
        'voice_turns_total{status="success"} 1.0',
        'voice_notes_total{status="success"} 1.0',
        "voice_turn_duration_seconds_count 1.0",
        "voice_audio_duration_seconds_count 1.0",
        "voice_stt_duration_seconds_count 1.0",
        "voice_hermes_duration_seconds_count 1.0",
        "voice_tts_duration_seconds_count 1.0",
        "voice_active_turns 1.0",
    ):
        assert name in text, f"missing: {name}"
