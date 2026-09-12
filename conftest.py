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
