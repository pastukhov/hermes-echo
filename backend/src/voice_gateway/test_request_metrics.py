"""Tests for the request-level metrics middleware (ТЗ §34).

The middleware (app.py, ``@app.middleware("http")``) must, for EVERY request
— success AND error — record:

- ``active_requests`` gauge: inc before processing, dec after (and the
  gauge must return to its pre-request value once the request finishes,
  even on errors);
- ``request_count_total{client_id, route, status}`` counter;
- ``request_latency_seconds{endpoint, status}`` histogram;
- ``request_count_by_route_total{route}`` counter.

Like ``test_app_stream.py`` the tests drive the ASGI app in-process via
``httpx.ASGITransport`` — no sockets, one event loop per request.

Two environment facts the tests must account for (both PRE-EXISTING, not
introduced by the metrics middleware):

1. ``app.py`` instantiates the abstract ``ArchiveStore`` at import time
   (``store = ArchiveStore(root)`` in ``create_app()`` — the module-level
   ``app = create_app()`` runs at import). That line predates this task
   and is broken at HEAD (it instantiates an ABC; the API used at
   call sites — ``turn_dir`` / ``save_metadata`` — is
   ``MetadataArchiveStore``'s). Fixing it is a separate card's concern
   (out of scope here); below, the name is shimmed in the archive package
   BEFORE importing the app module, so ``create_app()`` (and the import-
   time production singleton) works in test scope.

2. The ``/metrics`` scrape ITSELF is a request: it passes through the
   middleware, so a scrape always observes ``active_requests >= 1``
   (itself). Assertions are written against that self-count: the gauge
   is ``>= 1`` on any scrape and exactly ``1`` (only the scrape) once
   no other request is in flight.
"""
import asyncio
import atexit
import os
import re
import sys
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI

from backend.src.voice_gateway import archive
from backend.src.voice_gateway.archive import MetadataArchiveStore
from backend.src.voice_gateway.hermes.fake import FakeHermes
from backend.src.voice_gateway.models import Transcript
from backend.src.voice_gateway.stt.base import STTProvider
from backend.src.voice_gateway.stt.fake import FakeSTT


def _shim_app_store_import() -> None:
    """Import-scope shim for the pre-existing ``ArchiveStore(root)`` bug.

    ``app.py`` does ``store = ArchiveStore(root)`` where ``ArchiveStore``
    is the ABC from ``archive/base.py`` — uninstantiable at import time
    (the module-level ``app = create_app()`` raises). The methods the
    endpoint actually calls (``turn_dir`` / ``save_metadata``) are exactly
    ``MetadataArchiveStore``'s, so aliasing the name is a faithful,
    minimal stand-in. The real fix (``app.py:131``) is a separate card;
    this test must not depend on it.

    Also pins ``ARCHIVE_ROOT`` to a throwaway dir for the production
    singleton the module creates at import.
    """
    scratch = Path(f"/tmp/voice-gw-test-archive-{uuid.uuid4().hex[:8]}")
    scratch.mkdir(parents=True, exist_ok=True)
    os.environ["ARCHIVE_ROOT"] = str(scratch)
    archive.ArchiveStore = MetadataArchiveStore  # type: ignore[assignment]

    def _restore() -> None:
        # Restore the ABC name; the app module keeps its working
        # (MetadataArchiveStore) store instance — that is intentional:
        # it is exactly what the endpoint's call sites need.
        from backend.src.voice_gateway.archive import ArchiveStore
        archive.ArchiveStore = ArchiveStore  # type: ignore[assignment]
        os.environ.pop("ARCHIVE_ROOT", None)

    atexit.register(_restore)


_shim_app_store_import()

from backend.src.voice_gateway.app import create_app  # noqa: E402

_VALID_HERMES_RAW = (
    '{"reply": "Готово.", "note": {"create": false, "title": "", '
    '"content": "", "tags": []}}'
)


def _make_app(tmp_path: Path, stt: STTProvider | None = None) -> FastAPI:
    return create_app(
        archive_root=tmp_path / "archive",
        stt=stt if stt is not None else
            FakeSTT(Transcript(text="тестовая расшифровка", language="ru")),
        hermes=FakeHermes(_VALID_HERMES_RAW),
    )


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                             base_url="http://test")


def _run(loop: asyncio.AbstractEventLoop, coro):
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _get(app: FastAPI, path: str, device: str | None = None) -> httpx.Response:
    headers = {"X-Device-Id": device} if device else {}

    async def run() -> httpx.Response:
        async with _client(app) as client:
            return await client.get(path, headers=headers)

    return _run(asyncio.new_event_loop(), run())


def _post_turn(app: FastAPI, pcm: bytes, device: str) -> httpx.Response:
    async def run() -> httpx.Response:
        async with _client(app) as client:
            return await client.post(
                "/api/v1/voice/turn", content=pcm,
                headers={"X-Device-Id": device,
                         "X-Sample-Rate": "16000",
                         "X-Channels": "1"})

    return _run(asyncio.new_event_loop(), run())


def _parse_metrics(text: str) -> dict:
    """Parse a Prometheus text scrape into
    ``{metric_name: {label_tuple: value}}`` (comments/HELP/TYPE dropped).

    Label sets are stored as a sorted tuple of ``(key, value)`` pairs
    (hashable, order-independent) rather than a raw ``dict`` — a ``dict``
    cannot be a dict key itself.
    """
    result: dict = {}
    current = ""
    for line in text.splitlines():
        if line.startswith("#"):
            parts = line.split()
            if len(parts) >= 4 and parts[2] not in ("HELP", "TYPE"):
                current = parts[2]
            continue
        if not line:
            continue
        m = re.match(r'^([a-zA-Z_:][a-zA-Z0-9_:]*)'
                    r'(\{([^}]*)\})? ([^ ]+)$', line)
        assert m, f"unparsable metrics line: {line!r}"
        name, labels_raw, value = m.group(1), m.group(3), m.group(4)
        labels: dict = {}
        if labels_raw is not None:
            for part in labels_raw.split(","):
                key, _, val = part.partition("=")
                labels[key.strip()] = val.strip('"')
        key_tuple = tuple(sorted(labels.items()))
        result.setdefault(current or name, {})[key_tuple] = float(value)
    return result


def _scrape(app: FastAPI) -> dict:
    """Run one /metrics scrape and parse the body.

    A scrape itself is a request — it passes through the middleware and is
    recorded, exactly like a real Prometheus scrape in production.
    """
    resp = _get(app, "/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers.get("content-type", "")
    return _parse_metrics(resp.text)


def _gauge_value(metrics_text: str) -> float:
    for line in metrics_text.splitlines():
        if line.startswith("active_requests "):
            return float(line.split()[1])
    raise AssertionError("active_requests gauge not found in scrape")


def _sample(samples: dict, **want: str) -> float:
    """Value of the sample whose labels contain ALL of ``want``.

    ``samples`` maps a sorted ``(key, value)`` label tuple to the metric
    value (see :func:`_parse_metrics`).
    """
    for label_tuple, value in samples.items():
        labels = dict(label_tuple)
        if all(labels.get(k) == v for k, v in want.items()):
            return value
    raise AssertionError(f"no sample with labels {want!r} in {samples!r}")


def test_metrics_endpoint_still_exposes_voice_metrics(tmp_path: Path) -> None:
    """The existing /metrics endpoint stays intact (task body): the
    pre-existing voice_* metrics are still exposed alongside the new
    request-level ones.

    ``voice_turns_total`` is a labeled counter (``status``) that only
    gains a sample once a turn has actually been recorded (prometheus_client
    never emits a data line for a labeled metric before its first
    ``.labels(...)`` call) — unrelated to this task's middleware, so a turn
    is driven through first to observe it alongside the unlabeled
    metrics that are always present.
    """
    app = _make_app(tmp_path)
    resp = _post_turn(app, b"\x00" * 4000, "dev-baseline")
    assert resp.status_code == 200

    before = _scrape(app)
    # Pre-existing metrics (ТЗ §34 MVP set) untouched:
    assert "voice_turns_total" in before
    assert "voice_active_turns" in before
    assert "voice_turn_duration_seconds" in before
    # New request-level metrics present in the same scrape:
    assert "active_requests" in before
    assert "request_count_total" in before
    assert "request_latency_seconds" in before
    assert "request_count_by_route_total" in before


def test_request_metrics_recorded_on_success(tmp_path: Path) -> None:
    """A successful turn records all four request-level metrics (ТЗ §34)."""
    app = _make_app(tmp_path)
    resp = _post_turn(app, b"\x00" * 4000, "dev-metrics")
    assert resp.status_code == 200

    after = _scrape(app)

    # The turn AND the /metrics scrape itself are counted — both are
    # requests through the middleware; >= 1.0 for the exact turn labels.
    assert _sample(after["request_count_total"],
                   client_id="dev-metrics",
                   route="/api/v1/voice/turn",
                   status="200") >= 1.0
    # histogram: the _total sample counts observations
    assert _sample(after["request_latency_seconds"],
                   endpoint="/api/v1/voice/turn",
                   status="200") >= 1.0
    assert _sample(after["request_count_by_route_total"],
                   route="/api/v1/voice/turn") >= 1.0
    # Gauge back to baseline: the turn finished, so only the scrape itself
    # is in flight (self-count == 1.0, never the turn's 1 on top of it).
    assert after["active_requests"].get(()) == 1.0


def test_request_metrics_recorded_on_error(tmp_path: Path) -> None:
    """A 502 (odd-byte body → audio_invalid) is recorded with status 502 —
    the error case goes through the same middleware (task body: handle both
    success and error cases)."""
    app = _make_app(tmp_path)
    resp = _post_turn(app, b"\x00" * 3, "dev-err")  # odd byte count
    assert resp.status_code == 502

    after = _scrape(app)
    assert _sample(after["request_count_total"],
                   client_id="dev-err",
                   route="/api/v1/voice/turn",
                   status="502") >= 1.0
    assert _sample(after["request_latency_seconds"],
                   endpoint="/api/v1/voice/turn",
                   status="502") >= 1.0
    # Error path must not leak the gauge: only the scrape itself remains.
    assert after["active_requests"].get(()) == 1.0


def test_request_metrics_on_404_and_unknown_device(tmp_path: Path) -> None:
    """Unmatched paths fall back to the bounded 'unmatched' route label
    (never arbitrary path text) and a missing X-Device-Id becomes
    'unknown' — cardinality stays bounded (ТЗ §34 label discipline)."""
    app = _make_app(tmp_path)
    resp = _get(app, "/does/not/exist")
    assert resp.status_code == 404

    after = _scrape(app)
    assert _sample(after["request_count_total"],
                   client_id="unknown",
                   route="unmatched",
                   status="404") >= 1.0
    assert _sample(after["request_count_by_route_total"],
                   route="unmatched") >= 1.0
    assert after["active_requests"].get(()) == 1.0


def test_active_requests_gauge_tracks_in_flight(tmp_path: Path) -> None:
    """The gauge is inc'ed BEFORE processing and dec'ed AFTER (task body).

    While one turn request is genuinely in flight (body still streaming —
    the only in-flight window that coexists with a concurrent scrape,
    since STT is a sync call on the event loop, app.py:362) a concurrent
    /metrics scrape must see the turn's +1 on top of its own self-count:
    gauge == 2. Once the turn finishes, the gauge is back to exactly the
    scrape's self-count: 1.
    """
    app = _make_app(tmp_path)

    async def _slow_body():
        for _ in range(8):
            yield b"\x00" * 512
            await asyncio.sleep(0.05)

    async def run() -> tuple[float, float]:
        client = _client(app)
        try:
            req = client.build_request(
                "POST", "/api/v1/voice/turn",
                content=_slow_body(),
                headers={"X-Device-Id": "dev-slow",
                         "X-Sample-Rate": "16000",
                         "X-Channels": "1"})
            turn = asyncio.ensure_future(client.send(req))
            # Let the turn start streaming (it parks between body reads),
            # then scrape concurrently: the scrape sees the in-flight turn
            # (+1) plus itself (+1) == 2.
            await asyncio.sleep(0.2)
            mid_text = (await client.get("/metrics")).text
            mid = _gauge_value(mid_text)
            assert mid == 2.0, f"active_requests mid-flight: {mid!r}"
            resp = await turn
            assert resp.status_code == 200
            after_text = (await client.get("/metrics")).text
        finally:
            await client.aclose()
        return mid, _gauge_value(after_text)

    mid, after = _run(asyncio.new_event_loop(), run())
    assert mid == 2.0
    assert after == 1.0
