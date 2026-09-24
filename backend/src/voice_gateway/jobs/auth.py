"""Per-device bearer token mapping for protocol v2."""

from __future__ import annotations

import hmac
import json
from collections.abc import Mapping


def parse_device_tokens(value: str | None) -> dict[str, str]:
    if not value:
        return {}
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("VOICE_DEVICE_TOKENS must be a JSON object") from exc
    if not isinstance(decoded, dict) or not all(
        isinstance(device, str) and isinstance(token, str) and token
        for device, token in decoded.items()
    ):
        raise ValueError("VOICE_DEVICE_TOKENS must map device IDs to non-empty tokens")
    return decoded


def owns_device(
    device_id: str,
    authorization: str | None,
    device_tokens: Mapping[str, str],
) -> bool:
    expected = device_tokens.get(device_id)
    if expected is None:
        return False
    scheme, separator, supplied = (authorization or "").partition(" ")
    return bool(
        separator
        and scheme.lower() == "bearer"
        and supplied
        and hmac.compare_digest(supplied, expected)
    )
