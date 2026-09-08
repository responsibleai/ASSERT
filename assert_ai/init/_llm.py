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
    temperature: float = 1,
    max_tokens: int = 4096,
    response_format: dict[str, str] | None = None,
    web_search: bool = False,
) -> str:
    """Call litellm.completion synchronously and return the content string.

    When ``web_search`` is True, the turn is routed through the shared
    ``model_client.generate`` path so the design agent can do live web
    research via the OpenAI/Azure Responses API ``web_search_preview``
    tool (the same transport the ``systematize`` pipeline stage uses).
    Otherwise the fast Chat Completions path below is used unchanged.

    Raises:
        LLMAuthError: Bad API key or credentials.
        LLMInputError: Invalid request (prompt too long, bad params).
        LLMRateLimitError: Rate limited.
        LLMProviderError: Provider-side error (5xx).
    """
    if web_search:
        return _chat_completion_web_search(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    from assert_ai.core.model_client import (
        _ResponsesApiNotAvailableError,
        _activate_chat_completions_fallback,
        _classify_llm_error,
        _force_chat_completions,
        _get_litellm_module,
        _maybe_inject_azure_aad_token,
    )

    litellm = _get_litellm_module()

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format is not None:
        kwargs["response_format"] = response_format

    # Route ``azure/*`` calls through the same AAD injection path that
    # the main pipeline uses (see ``_build_chat_payload``). Without
    # this, ``assert-ai init`` would bypass AAD and silently fall back
    # to whatever key/cred LiteLLM finds in the environment, defeating
    # the documented ``ASSERT_AZURE_USE_AAD=1`` opt-in.
    _maybe_inject_azure_aad_token(model, kwargs)

    try:
        response = litellm.completion(**kwargs)
    except Exception as exc:
        classified = _classify_llm_error(exc, model=model)
        # One-shot fallback: if the Responses API is not available in
        # this region, activate process-wide Chat Completions and
        # retry once. If the fallback was already active when we
        # entered, this isn't a routing problem — re-raise.
        #
        # NOTE: This intentionally duplicates the one-shot fallback
        # pattern from ``model_client._with_retries`` rather than
        # sharing code. The init agent runs a single LLM call up-front
        # (no per-task retry budget, no streaming, no structured
        # output), so wiring it through the full ``_with_retries``
        # machinery would add more coupling than the ~20 LoC saves.
        if isinstance(classified, _ResponsesApiNotAvailableError):
            if _force_chat_completions:
                raise classified from exc
            _activate_chat_completions_fallback(
                "Azure Responses API not enabled in region",
                model=model,
            )
            # Reuse the original kwargs verbatim. ``_activate_chat_completions_fallback``
            # flips process-wide routing state, so the same call now goes through
            # Chat Completions instead of the Responses API — no per-call kwargs
            # changes are needed.
            try:
                response = litellm.completion(**kwargs)
            except Exception as inner_exc:
                raise _classify_llm_error(inner_exc) from inner_exc
        else:
            raise classified from exc

    content = response.choices[0].message.content
    if content is None:
        content = ""
    return content.strip()


def web_search_available(model: str) -> bool:
    """Whether ``model`` can drive the design agent's live web research.

    Web search rides the OpenAI/Azure Responses API ``web_search_preview``
    tool, so it is gated to those model families (the same gate the
    pipeline stages use). Returns False for other providers so the CLI can
    degrade to a knowledge-only conversation with a warning instead of
    crashing mid-run.
    """
    from assert_ai.core.model_client import _supports_web_search_preview

    return _supports_web_search_preview(model)


def _chat_completion_web_search(
    *,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
) -> str:
    """Run one design-agent turn with live web search enabled.

    Routes through the shared ``model_client.generate`` path so the init
    agent reuses the pipeline's web-search transport: the OpenAI/Azure
    Responses API ``web_search_preview`` tool, with the same automatic
    degradation to Chat Completions (dropping web grounding) when the
    Responses API is unavailable in the region. JSON-shape reliability is
    left to the design loop's existing parse-and-retry protocol rather
    than a forced ``response_format`` — the Responses API path constrains
    output shape differently, and the loop already re-asks on malformed
    JSON.
    """
    from assert_ai.core.model_client import GenerateOptions, generate
    from assert_ai.core.runtime_safety import run_stage_coro

    options = GenerateOptions(
        temperature=temperature,
        max_tokens=max_tokens,
        web_search=True,
        call_label="init-design-agent",
    )
    response = run_stage_coro(generate(model, messages, options))
    return (response.text or "").strip()


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
