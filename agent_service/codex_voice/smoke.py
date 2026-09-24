"""One-request Codex SDK smoke test; writes only non-secret diagnostics."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any

from .config import RuntimeConfig
from .runtime import CodexRuntime, RuntimeFailure


PROMPT = "Проверка связи, ответь одним словом"
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "smoke-result.json"


async def run_smoke(runtime: Any | None = None) -> dict[str, Any]:
    active_runtime = runtime or CodexRuntime(RuntimeConfig.from_env())
    started = time.perf_counter()
    report: dict[str, Any] = {
        "provider": active_runtime.provider,
        "sdk_version": active_runtime.sdk_version,
        "runtime_version": active_runtime.runtime_version,
        "model": None,
        "duration_ms": 0,
    }
    try:
        await active_runtime.start()
        report["model"] = active_runtime.model
        thread_id = await active_runtime.start_thread(ephemeral=True)
        report["reply"] = await active_runtime.run(thread_id, PROMPT)
        report["ok"] = True
    except RuntimeFailure as failure:
        report.update(ok=False, error_code=failure.code, error=failure.args[0])
    finally:
        report["duration_ms"] = round((time.perf_counter() - started) * 1000)
        await active_runtime.close()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    report = asyncio.run(run_smoke())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
