"""Thin synchronous LLM caller for the init design agent."""

from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger(__name__)


def chat_completion(
    *,
    model: str,
    messages: list[dict[str, str]],
    temperature: float = 0.7,
    max_tokens: int = 4096,
    response_format: dict[str, str] | None = None,
) -> str:
    """Call litellm.completion synchronously and return the content string.

    Raises:
        LLMAuthError: Bad API key or credentials.
        LLMInputError: Invalid request (prompt too long, bad params).
        LLMRateLimitError: Rate limited.
        LLMProviderError: Provider-side error (5xx).
    """
    import litellm

    from p2m.core.model_client import _classify_llm_error

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format is not None:
        kwargs["response_format"] = response_format

    try:
        response = litellm.completion(**kwargs)
    except Exception as exc:
        raise _classify_llm_error(exc) from exc

    content = response.choices[0].message.content
    if content is None:
        content = ""
    return content.strip()


def chat_completion_json(
    *,
    model: str,
    messages: list[dict[str, str]],
    temperature: float = 0.7,
    max_tokens: int = 4096,
) -> dict[str, Any]:
    """Call litellm.completion with JSON response format and parse the result.

    Returns the parsed JSON dict. Raises ValueError if the response is
    not valid JSON.
    """
    raw = chat_completion(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
    )
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        log.warning("LLM returned invalid JSON: %s", raw[:200])
        raise ValueError(f"LLM returned invalid JSON: {exc}") from exc

    if not isinstance(result, dict):
        raise ValueError(f"Expected JSON object, got {type(result).__name__}")
    return result
