# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Redact credential-shaped strings at untrusted persistence boundaries."""
from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any

log = logging.getLogger(__name__)

_CREDENTIAL_PATTERNS = re.compile(
    r"("
    r"Bearer\s+[A-Za-z0-9\-._~+/]+=*"
    r"|Basic\s+[A-Za-z0-9+/]+=*"
    r"|(?:sk|pk|api|key|token|secret)[-_][A-Za-z0-9\-._]{20,}"
    r"|(?:api[_-]?key|auth[_-]?token|secret|password|access[_-]?token|refresh[_-]?token"
    r"|client[_-]?secret|authorization)[\"':\s=]+[A-Za-z0-9\-._~+/]{16,}"
    r")",
    re.IGNORECASE,
)

REDACTED = "[REDACTED]"


def sanitize_untrusted_text(text: str) -> str:
    """Redact credential-like patterns before text is persisted."""
    if not text:
        return text
    sanitized = _CREDENTIAL_PATTERNS.sub(REDACTED, text)
    if sanitized != text:
        log.warning("Credential-like patterns detected and redacted from untrusted data")
    return sanitized


def sanitize_untrusted_value(value: Any) -> Any:
    """Recursively redact credential-like strings in JSON-shaped data."""
    if isinstance(value, str):
        return sanitize_untrusted_text(value)
    if isinstance(value, list):
        return [sanitize_untrusted_value(item) for item in value]
    if isinstance(value, Mapping):
        sanitized: dict[Any, Any] = {}
        original_keys = set(value.keys())
        redacted_key_index = 0
        for key, item in value.items():
            safe_key = key
            if isinstance(key, str):
                redacted_key = sanitize_untrusted_text(key)
                if redacted_key != key:
                    while True:
                        safe_key = f"[REDACTED_KEY_{redacted_key_index}]"
                        redacted_key_index += 1
                        if safe_key not in sanitized and safe_key not in original_keys:
                            break
            sanitized[safe_key] = sanitize_untrusted_value(item)
        return sanitized
    return value
