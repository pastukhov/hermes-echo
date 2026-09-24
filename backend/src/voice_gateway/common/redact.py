"""Redaction utilities structured request logging middleware.

Provides _is_sensitive_header _redact_header middleware to
redact sensitive header values before appear log output.
"""

# Headers considered sensitive, redacted
_SENSITIVE_HEADER_NAMES = {
    "authorization",
    "cookie",
    "x-api-key",
}


def _is_sensitive_header(name: str) -> bool:
    """Check whether header name is considered sensitive.

    Args:
        name: Header name to check (case-insensitive).

    Returns:
        True if header name is sensitive set.
    """
    return name.lower() in _SENSITIVE_HEADER_NAMES


def _redact_header(value: str) -> str:
    """Redact sensitive header value, showing only first and last chars.

    Args:
        value: header value to redact.

    Returns:
        Redacted value: first char '***' last char, '***' short.
    """
    if not value:
        return "***"
    if len(value) <= 3:
        return "***"
    return value[0] + "***" + value[-1]