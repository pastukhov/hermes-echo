"""Pytest bootstrap: make the repo root importable.

The backend tree uses absolute ``backend.*`` imports (e.g.
``backend.src.voice_gateway.models``). The repo root is not a package and
no packaging manifest installs it, so tests are run from the repo root and
this conftest puts the root on ``sys.path`` for the pytest process.
"""
import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# NOTE on the "no live network calls" requirement (card t_825bfaca): an
# autouse ``socket.socket.connect``/``connect_ex`` monkeypatch was tried
# here and rejected — it broke unrelated, already-passing tests
# (test_turn_success and friends) because the in-process ASGI/event-loop
# plumbing (anyio/asyncio self-pipe sockets used by httpx.ASGITransport)
# also routes through ``socket.socket``, so blocking it silently starves
# the app's own request handling rather than catching a stray outbound
# call. Per the card's own acceptance criteria ("добавить guard только
# если это совместимо с существующим suite; иначе — доказательство через
# полностью injected MockTransport и audit"), the guard is skipped in
# favor of the audit: every HTTP-capable test in this repo already drives
# its client through ``httpx.MockTransport`` (stt/test_client.py) or
# in-process ``httpx.ASGITransport`` (test_app_stream.py,
# test_hermes_pipeline.py) — neither opens a real OS socket to an
# external host, so the full suite runs deterministically offline as-is.
