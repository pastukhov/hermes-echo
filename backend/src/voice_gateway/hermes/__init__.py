"""Hermes stage (ТЗ sections 16, 21–24)."""
from backend.src.voice_gateway.hermes.base import HermesClient, HermesClientError
from backend.src.voice_gateway.hermes.client import OpenAICompatibleHermesClient
from backend.src.voice_gateway.hermes.validation import (
    HermesValidationError,
    parse_hermes_response,
)

__all__ = [
    "HermesClient",
    "HermesClientError",
    "OpenAICompatibleHermesClient",
    "HermesValidationError",
    "parse_hermes_response",
]
