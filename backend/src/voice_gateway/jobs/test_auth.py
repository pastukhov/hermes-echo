from __future__ import annotations

import pytest

from .auth import owns_device, parse_device_tokens


def test_device_tokens_are_mapped_and_compared_without_cross_device_access():
    tokens = parse_device_tokens('{"mic-a":"secret-a","mic-b":"secret-b"}')
    assert owns_device("mic-a", "Bearer secret-a", tokens)
    assert not owns_device("mic-b", "Bearer secret-a", tokens)
    assert not owns_device("unknown", "Bearer secret-a", tokens)
    assert not owns_device("mic-a", "secret-a", tokens)


def test_malformed_device_token_map_is_rejected():
    with pytest.raises(ValueError, match="JSON object"):
        parse_device_tokens("not-json")
