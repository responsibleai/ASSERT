# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""LiteLLM-backed model helpers for the measurements pipeline.

Import side effects (intentional for the ``assert-ai`` CLI):

* ``_normalize_azure_api_base()`` rewrites ``AZURE_API_BASE`` at import
  time to strip any ``/openai/...`` path suffix, logging at INFO when
  it does so. Library users importing this module will see the env
  var mutated for the rest of the process.
* ``_configure_azure_auth_mode()`` reads ``ASSERT_AZURE_USE_AAD`` and
  ``AZURE_API_KEY`` once at import time to pick the Azure OpenAI auth
  mode for the process. When the resolved mode is AAD, it pre-warms
  the ``azure-identity`` credential so first-request latency stays
  low and missing-dep errors surface early. The mode is read again
  per-request only as a cheap module-global lookup, never re-derived.
* The first activation of the Chat Completions fallback (either via
  ``ASSERT_PREFER_CHAT_COMPLETIONS=1`` at import time, an explicit
  API preference, or a reactive recovery from a region error) calls
  ``_install_responses_api_guard()``, which monkey-patches
  ``litellm.main.responses_api_bridge_check`` for the rest of the
  process. The patch is idempotent and forwards all positional and
  keyword arguments to the original function so it survives minor
  LiteLLM upgrades that add new kwargs.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import importlib
import json
import logging
import os
import random
import time
from dataclasses import asdict, dataclass, field, is_dataclass, replace
from typing import Any, Iterator, Mapping, Sequence

from assert_ai.core import azure_auth

log = logging.getLogger(__name__)

# ── Types ──────────────────────────────────────────────────────

MessageLike = "Message | Mapping[str, Any]"
ToolCallLike = "ToolCall | Mapping[str, Any]"


@dataclass(slots=True)
class ToolCall:
    """Normalized tool call representation."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    call_id: str | None = None
    raw_arguments: str | None = None
    type: str = "function"
    raw: Any = None

    @property
    def id(self) -> str | None:
        return self.call_id

    @property
    def function(self) -> str:
        return self.name

    def to_openai_dict(self) -> dict[str, Any]:
        payload = {
            "type": self.type,
            "function": {
                "name": self.name,
                "arguments": self.raw_arguments or json.dumps(self.arguments),
            },
        }
        if self.call_id:
            payload["id"] = self.call_id
        return payload


@dataclass(slots=True)
class Message:
    """OpenAI-style message shape."""

    role: str
    content: Any
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)

    @property
    def text(self) -> str:
        if isinstance(self.content, str):
            return self.content
        if isinstance(self.content, list):
            parts: list[str] = []
            for item in self.content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(item, str):
                    parts.append(item)
            return "\n".join(parts)
        return str(self.content or "")

    def to_openai_dict(self) -> dict[str, Any]:
        payload = {
            "role": self.role,
            "content": self.content,
        }
        if self.name:
            payload["name"] = self.name
        if self.tool_call_id:
            payload["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            payload["tool_calls"] = [tool_call.to_openai_dict() for tool_call in self.tool_calls]
        return payload


@dataclass(slots=True)
class UsageStats:
    """Normalized token accounting.

    ``cached_input_tokens`` is the count of input tokens served from the
    provider's prompt cache (a subset of ``prompt_tokens``). It surfaces
    OpenAI/Azure ``prompt_tokens_details.cached_tokens`` and
    Anthropic ``cache_read_input_tokens`` under one field so callers can
    measure prefix-cache effectiveness without branching on the provider.

    ``cache_creation_input_tokens`` is Anthropic's "wrote new entries to
    the cache" counter; it has no OpenAI/Azure equivalent because their
    cache is implicit and free to write.
    """

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cached_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    raw: Any = None


@dataclass(slots=True)
class UsageAccumulator:
    """Aggregate token usage and call counts across many ``generate*`` calls.

    Created by :func:`track_usage` and populated by the model client itself, so
    callers don't have to thread per-call usage objects through their code.
    Per-model breakdowns are tracked under ``per_model`` so a single stage that
    invokes more than one model (e.g. test_set + stratification) can be inspected later.
    """

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    per_model: dict[str, dict[str, int]] = field(default_factory=dict)

    def add(self, usage: UsageStats | None, *, model: str | None = None) -> None:
        """Fold one call's normalized usage into this accumulator."""
        if usage is None:
            return
        self.calls += 1
        ipt = int(usage.prompt_tokens or 0)
        opt = int(usage.completion_tokens or 0)
        cit = int(usage.cached_input_tokens or 0)
        cct = int(usage.cache_creation_input_tokens or 0)
        self.input_tokens += ipt
        self.output_tokens += opt
        self.cached_input_tokens += cit
        self.cache_creation_input_tokens += cct
        key = model or "?"
        bucket = self.per_model.setdefault(
            key,
            {
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cached_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        )
        bucket["calls"] += 1
        bucket["input_tokens"] += ipt
        bucket["output_tokens"] += opt
        bucket["cached_input_tokens"] += cit
        bucket["cache_creation_input_tokens"] += cct

    def cache_hit_rate(self) -> float:
        """Return cached_input_tokens / input_tokens, or 0.0 when no input tokens."""
        if self.input_tokens <= 0:
            return 0.0
        return self.cached_input_tokens / self.input_tokens

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable snapshot of this accumulator."""
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "cache_hit_rate": self.cache_hit_rate(),
            "per_model": dict(self.per_model),
        }


_USAGE_ACCUMULATOR: contextvars.ContextVar[UsageAccumulator | None] = contextvars.ContextVar(
    "_assert_ai_usage_accumulator",
    default=None,
)


@contextlib.contextmanager
def track_usage() -> Iterator[UsageAccumulator]:
    """Capture token usage from every ``generate*`` call within the block.

    Uses a ``ContextVar`` so that ``asyncio.run(...)`` blocks invoked inside the
    ``with`` statement inherit the accumulator and concurrent tasks all add into
    the same object. The accumulator only sees calls made on the same ``async``
    stack (or the same thread) — independent threads or coroutines that are
    started in a fresh context will not contribute.
    """
    accumulator = UsageAccumulator()
    token = _USAGE_ACCUMULATOR.set(accumulator)
    try:
        yield accumulator
    finally:
        _USAGE_ACCUMULATOR.reset(token)


def _record_usage(usage: UsageStats | None, *, model: str | None) -> None:
    """Push one normalized usage payload into the active accumulator, if any."""
    accumulator = _USAGE_ACCUMULATOR.get()
    if accumulator is None:
        return
    accumulator.add(usage, model=model)


@dataclass(slots=True)
class GenerateOptions:
    """Transport options shared across chat, tool, and responses calls."""

    temperature: float | None = None
    max_tokens: int | None = None
    max_output_tokens: int | None = None
    web_search: bool = False
    reasoning_effort: str | None = None
    tool_choice: str | dict[str, Any] | None = None
    timeout_s: float | None = None
    call_label: str | None = None
    extra_kwargs: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ModelResponse:
    """Normalized model response."""

    text: str = ""
    content: Any = None
    parsed: Any = None
    reasoning: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    status: str | None = None
    incomplete_details: Any = None
    model: str | None = None
    response_id: str | None = None
    usage: UsageStats | None = None
    api_mode: str | None = None
    request_payload: dict[str, Any] | None = None
    raw: Any = None

    @property
    def message(self) -> Message:
        return Message(role="assistant", content=self.text, tool_calls=list(self.tool_calls))


# ── Transport helpers ──────────────────────────────────────────

_LITELLM_MODULE: Any | None = None


# Finish-reason values that indicate the model hit the output-token limit
# rather than completing its response. ``length`` is Chat Completions;
# ``max_tokens`` / ``max_output_tokens`` come from the OpenAI Responses API
# (surfaced via ``incomplete_details.reason`` in ``normalize_response``).
# We deliberately do NOT include bare ``"incomplete"`` (the Responses API
# ``status`` field) because that value covers any non-finished state -- including
# content-filter refusals or provider errors that won't be fixed by enlarging
# the token budget. Truncation must be confirmed by a specific ``reason``.
_TRUNCATED_FINISH_REASONS: frozenset[str] = frozenset({
    "length",
    "max_tokens",
    "max_output_tokens",
})


def is_truncated_response(response: "ModelResponse") -> bool:
    """Return True iff *response* hit the model's output token limit.

    Checks both the normalized ``finish_reason`` (which already prefers the
    Responses API ``incomplete_details.reason`` over the ambiguous
    ``status='incomplete'``) and the raw ``incomplete_details.reason`` as a
    belt-and-suspenders fallback for callers that bypass ``normalize_response``.
    """
    reason = getattr(response, "finish_reason", None)
    if isinstance(reason, str) and reason in _TRUNCATED_FINISH_REASONS:
        return True
    incomplete = getattr(response, "incomplete_details", None)
    incomplete_reason = _get_value(incomplete, "reason")
    return isinstance(incomplete_reason, str) and incomplete_reason in _TRUNCATED_FINISH_REASONS


def build_json_schema_response_format(
    name: str,
    schema: dict[str, Any],
    *,
    strict: bool = True,
) -> dict[str, Any]:
    """Build OpenAI-format JSON schema output config."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": strict,
            "schema": schema,
        },
    }


def build_json_schema_text_format(
    name: str,
    schema: dict[str, Any],
    *,
    strict: bool = True,
) -> dict[str, Any]:
    """Build Responses API JSON schema output config."""
    return {
        "type": "json_schema",
        "name": name,
        "strict": strict,
        "schema": schema,
    }


def _model_family(model: str) -> str:
    """Return the provider/model-family prefix used for transport capability checks."""
    normalized = (model or "").strip().lower()
    if "/" in normalized:
        return normalized.split("/", 1)[0]
    if normalized.startswith(("gpt-", "o1", "o3", "o4")):
        return "openai"
    return normalized


# Resolved once at import time by ``_configure_azure_auth_mode()``.
# Kept module-global so per-request lookups stay free (no env reads,
# no extra function calls in the hot path).
_AZURE_AUTH_MODE: azure_auth.Mode | None = None

# True when the user's environment selects AAD (explicit or fallback)
# but the ``azure-identity`` package is not importable. Used by
# ``_classify_llm_error`` to swap the RBAC hint for an install hint.
_AZURE_AAD_DEP_MISSING: bool = False


def _maybe_inject_azure_aad_token(model: str, payload: dict[str, Any]) -> None:
    """Attach an Azure AD token provider to ``azure/*`` payloads when AAD is active.

    No-op for non-Azure models and for the ``key`` auth mode, so existing
    API-key users are completely unaffected.

    When the resolved mode is ``aad`` (explicit opt-in via
    ``ASSERT_AZURE_USE_AAD=1``) but ``azure-identity`` is not installed,
    this raises :class:`LLMAuthError` eagerly so the user gets a clear
    install hint instead of a confusing 401 from the gateway.

    When the resolved mode is ``aad-fallback`` (opportunistic — no key
    set, no explicit opt-in) and the dep is missing, we silently skip
    injection and let LiteLLM surface its own auth error; the friendly
    install hint is then appended in :func:`_classify_llm_error`.

    Callers must invoke this *before* applying ``extra_kwargs`` so that
    any explicit user-supplied ``azure_ad_token_provider`` still wins.
    """
    if _model_family(model) != "azure":
        return
    if _AZURE_AUTH_MODE == "key":
        return
    provider = azure_auth.get_azure_token_provider()
    if provider is None:
        if _AZURE_AUTH_MODE == "aad":
            raise LLMAuthError(
                "Azure managed-identity auth is active but the "
                "azure-identity package is not installed. Install it "
                "with `pip install 'assert-ai[azure-aad]'`, or set "
                "AZURE_API_KEY to use API-key auth."
            )
        # aad-fallback path: silently skip — LiteLLM's own auth error
        # will be augmented with an install hint by _classify_llm_error.
        return
    payload["azure_ad_token_provider"] = provider


def _supports_web_search_preview(model: str) -> bool:
    """Whether this model can use the Responses API web_search_preview tool.

    The current implementation sends OpenAI Responses API payloads with
    ``tools=[{"type": "web_search_preview"}]``. LiteLLM exposes that
    path for OpenAI-compatible models, but provider smoke testing showed
    Gemini fails before useful generation when this tool is combined with
    structured output. Keep the gate intentionally narrow until each
    provider has an explicit, tested web-search path.
    """
    return _model_family(model) in {"openai", "azure"}


def _require_web_search_preview_support(model: str) -> None:
    if _supports_web_search_preview(model):
        return
    raise ValueError(
        "web_search uses the OpenAI Responses API web_search_preview tool, "
        f"which is not enabled for model '{model}'. Disable web_search for this "
        "stage or use an OpenAI/Azure OpenAI model."
    )


def messages_to_openai(messages: str | Sequence[MessageLike]) -> list[dict[str, Any]]:
    """Convert message inputs into OpenAI-format dicts."""
    if isinstance(messages, str):
        return [Message(role="user", content=messages).to_openai_dict()]

    result: list[dict[str, Any]] = []
    for message in messages:
        result.append(_coerce_message(message).to_openai_dict())
    return result


def normalize_tool_calls(raw_tool_calls: Sequence[ToolCallLike] | None) -> list[ToolCall]:
    """Convert raw OpenAI/LiteLLM tool calls into ``ToolCall`` objects."""
    normalized: list[ToolCall] = []
    for raw_tool_call in raw_tool_calls or []:
        function_payload = _get_value(raw_tool_call, "function") or raw_tool_call
        raw_arguments = _get_value(function_payload, "arguments")
        parsed_arguments: dict[str, Any] = {}
        if isinstance(raw_arguments, str) and raw_arguments.strip():
            try:
                parsed = json.loads(raw_arguments)
                if isinstance(parsed, dict):
                    parsed_arguments = parsed
            except json.JSONDecodeError:
                pass
        elif isinstance(raw_arguments, dict):
            parsed_arguments = dict(raw_arguments)

        normalized.append(
            ToolCall(
                name=str(_get_value(function_payload, "name") or ""),
                arguments=parsed_arguments,
                call_id=_get_value(raw_tool_call, "id"),
                raw_arguments=raw_arguments if isinstance(raw_arguments, str) else None,
                type=str(_get_value(raw_tool_call, "type") or "function"),
                raw=raw_tool_call,
            )
        )
    return normalized


def normalize_response(
    raw_response: Any,
    *,
    api_mode: str | None = None,
    request_payload: dict[str, Any] | None = None,
) -> ModelResponse:
    """Normalize a LiteLLM/OpenAI-style response object."""
    choice = _first_choice(raw_response)
    message = _get_value(choice, "message")
    content = _get_value(message, "content")

    if content in (None, ""):
        content = _get_value(raw_response, "output_text")

    if content in (None, ""):
        content = _extract_responses_output_text(_get_value(raw_response, "output"))

    text = _extract_text_content(content)
    reasoning = _extract_text_content(_get_value(message, "reasoning_content"))
    parsed = _maybe_parse_json(text)

    return ModelResponse(
        text=text,
        content=content,
        parsed=parsed,
        reasoning=reasoning,
        tool_calls=normalize_tool_calls(_get_value(message, "tool_calls")),
        finish_reason=(
            _get_value(choice, "finish_reason")
            or _get_value(raw_response, "stop_reason")
            or _get_value(_get_value(raw_response, "incomplete_details"), "reason")
            or _get_value(raw_response, "status")
        ),
        status=_get_value(raw_response, "status"),
        incomplete_details=_get_value(raw_response, "incomplete_details"),
        model=_get_value(raw_response, "model"),
        response_id=_get_value(raw_response, "id"),
        usage=_normalize_usage(_get_value(raw_response, "usage")),
        api_mode=api_mode,
        request_payload=request_payload,
        raw=raw_response,
    )


def to_jsonable(value: Any) -> Any:
    """Convert provider payloads into JSON-safe structures for artifact storage."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if is_dataclass(value):
        return to_jsonable(asdict(value))
    if hasattr(value, "model_dump") and callable(value.model_dump):
        return to_jsonable(value.model_dump())
    if hasattr(value, "dict") and callable(value.dict):
        return to_jsonable(value.dict())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [to_jsonable(item) for item in value]
    if hasattr(value, "__dict__"):
        return to_jsonable(vars(value))
    return str(value)


def summarize_response(response: ModelResponse) -> dict[str, Any]:
    """Build a compact display summary for a model response."""
    payload: dict[str, Any] = {
        "content": response.text or "",
        "stop_reason": response.finish_reason or "",
    }
    if response.tool_calls:
        payload["tool_calls"] = [tool_call.to_openai_dict() for tool_call in response.tool_calls]
    if response.usage:
        usage_payload: dict[str, Any] = {
            "input_tokens": response.usage.prompt_tokens,
            "output_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
        }
        if response.usage.cached_input_tokens:
            usage_payload["cached_input_tokens"] = response.usage.cached_input_tokens
        if response.usage.cache_creation_input_tokens:
            usage_payload["cache_creation_input_tokens"] = (
                response.usage.cache_creation_input_tokens
            )
        payload["usage"] = usage_payload
    if response.model:
        payload["model"] = response.model
    if response.response_id:
        payload["response_id"] = response.response_id
    return payload


def build_llm_call_trace(response: ModelResponse, *, source: str) -> dict[str, Any]:
    """Build an artifact-safe owned LLM call trace.

    Sanitizes request payloads to prevent credential leakage in artifact files.
    """
    from assert_ai.core.security import sanitize_payload

    return {
        "source": source,
        "api_mode": response.api_mode or "",
        "request": sanitize_payload(to_jsonable(response.request_payload or {})),
        "response": to_jsonable(response.raw),
        "derived": summarize_response(response),
    }


def _coerce_message(message: MessageLike) -> Message:
    if isinstance(message, Message):
        return message

    tool_calls = normalize_tool_calls(_get_value(message, "tool_calls"))
    return Message(
        role=str(_get_value(message, "role") or "user"),
        content=_get_value(message, "content"),
        name=_get_value(message, "name"),
        tool_call_id=_get_value(message, "tool_call_id"),
        tool_calls=tool_calls,
    )


def _build_chat_payload(
    model: str,
    messages: str | Sequence[MessageLike],
    options: GenerateOptions | None,
) -> dict[str, Any]:
    resolved_options = options or GenerateOptions()
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages_to_openai(messages),
    }
    if resolved_options.temperature is not None:
        payload["temperature"] = resolved_options.temperature
    if resolved_options.max_tokens is not None:
        payload["max_tokens"] = resolved_options.max_tokens
    if resolved_options.max_output_tokens is not None and "max_tokens" not in payload:
        payload["max_tokens"] = resolved_options.max_output_tokens
    if resolved_options.reasoning_effort is not None:
        payload["reasoning_effort"] = resolved_options.reasoning_effort
    _maybe_inject_azure_aad_token(model, payload)
    payload.update(resolved_options.extra_kwargs)
    return payload


def _build_responses_payload(
    model: str,
    messages: str | Sequence[MessageLike],
    options: GenerateOptions | None,
) -> dict[str, Any]:
    resolved_options = options or GenerateOptions()
    if isinstance(messages, str):
        input_payload: Any = messages
    else:
        input_payload = messages_to_openai(messages)
    payload: dict[str, Any] = {
        "model": model,
        "input": input_payload,
    }
    if resolved_options.temperature is not None:
        payload["temperature"] = resolved_options.temperature
    if resolved_options.max_output_tokens is not None:
        payload["max_output_tokens"] = resolved_options.max_output_tokens
    elif resolved_options.max_tokens is not None:
        payload["max_output_tokens"] = resolved_options.max_tokens
    if resolved_options.reasoning_effort is not None:
        payload["reasoning_effort"] = resolved_options.reasoning_effort
    _maybe_inject_azure_aad_token(model, payload)
    payload.update(resolved_options.extra_kwargs)
    return payload


def _responses_client(litellm: Any) -> tuple[Any, bool]:
    if hasattr(litellm, "aresponses"):
        return litellm.aresponses, True
    if hasattr(litellm, "responses"):
        return litellm.responses, False
    raise ValueError("web_search requires a LiteLLM responses client")


def _get_litellm_module() -> Any:
    global _LITELLM_MODULE
    if _LITELLM_MODULE is None:
        try:
            _LITELLM_MODULE = importlib.import_module("litellm")
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "litellm is not installed. Install it with `python -m pip install litellm` "
                "before using assert_ai.core.model_client."
            ) from exc
        # Silence noisy litellm warnings that pollute stderr
        _LITELLM_MODULE.suppress_debug_info = True
        # Disable LiteLLM's internal retry so _with_retries is the
        # sole retry layer — avoids double-retry and lets the
        # coordinated per-model cooldown work correctly.
        _LITELLM_MODULE.num_retries = 0
        # If the user has opted into Chat Completions proactively,
        # disable the Responses API now so LiteLLM never attempts it.
        if os.environ.get("ASSERT_PREFER_CHAT_COMPLETIONS", "").strip() in ("1", "true", "yes"):
            _apply_chat_completions_preference()
            log.info(
                "ASSERT_PREFER_CHAT_COMPLETIONS is set; using Chat "
                "Completions API for all models."
            )
    return _LITELLM_MODULE


async def _await_with_timeout(awaitable: Any, *, timeout_s: float | None) -> Any:
    if timeout_s is None:
        return await awaitable
    async with asyncio.timeout(timeout_s):
        return await awaitable


async def _run_sync_with_timeout(callable_obj: Any, *, timeout_s: float | None, **kwargs: Any) -> Any:
    task = asyncio.to_thread(callable_obj, **kwargs)
    return await _await_with_timeout(task, timeout_s=timeout_s)


# ── LiteLLM error classification ──────────────────────────────

class LLMAuthError(Exception):
    """Bad API key or credentials — not retryable."""

class LLMInputError(Exception):
    """Invalid request (prompt too long, bad params) — not retryable."""

class LLMContentFilterError(LLMInputError):
    """Provider-side content filter rejected the prompt — not retryable.

    This is a *subclass* of LLMInputError so existing handlers that catch
    LLMInputError still see content-filter rejections. Adversarial-eval
    workloads (judge.py, rollout.py) catch this subclass specifically and
    treat it as a soft per-row failure rather than aborting the run, since
    sending content the provider will reject is the *whole point* of those
    workloads. Routine eval workloads still see the parent LLMInputError
    and fail loudly as before.
    """

class LLMRateLimitError(Exception):
    """Rate limited — retryable after backoff."""

class LLMProviderError(Exception):
    """Provider-side error (5xx) — may be retryable."""


class _ResponsesApiNotAvailableError(LLMProviderError):
    """Region does not support Azure Responses API — triggers automatic
    fallback to Chat Completions for the remainder of the run.

    Inherits from :class:`LLMProviderError` so that callers up the stack
    that catch ``LLMProviderError`` (notably ``stages/inference.py`` and
    ``init/_design_agent.py``) still treat this as a real failure when
    the in-loop fallback exhausts its retry and re-raises. Without this
    inheritance the exception falls through generic ``except Exception``
    catch-alls and silently produces empty content, which a judge then
    happily scores ✓.
    """


# ── Responses API → Chat Completions fallback state ────────────
_responses_api_fallback_warned: bool = False
"""Set to True after the first Responses-API-not-available warning is
emitted so we only log the user-facing message once per run."""

_web_search_drop_warned: bool = False
"""Set to True after the first ``web_search`` degradation warning so
the message is emitted once per run rather than once per task."""

_force_chat_completions: bool = False
"""When True, the monkey-patched ``responses_api_bridge_check`` forces
``mode=chat`` so LiteLLM never routes through the Responses API bridge."""

_responses_api_guard_installed: bool = False
"""Set to True after the bridge-check monkey-patch has been installed."""


def _install_responses_api_guard() -> None:
    """Monkey-patch ``litellm.main.responses_api_bridge_check``.

    LiteLLM 1.82+ auto-routes GPT-5.4+ calls that include both ``tools``
    and ``reasoning_effort`` through the Responses API bridge (see
    ``litellm/main.py:responses_api_bridge_check``). There is no
    kwarg or environment variable to opt out — the routing decision is
    made inside the bridge check itself.

    The patch wraps the original function and overrides the returned
    ``mode`` from ``"responses"`` back to ``"chat"`` whenever
    ``_force_chat_completions`` is True. Idempotent — safe to call
    multiple times; the guard flag prevents double-wrapping.
    """
    global _responses_api_guard_installed
    if _responses_api_guard_installed:
        return

    from litellm import main as _litellm_main  # noqa: WPS433

    _original = _litellm_main.responses_api_bridge_check

    # Accept ``*args, **kwargs`` and forward them as-is so the patch
    # is forward-compatible with LiteLLM minor releases that add new
    # parameters to ``responses_api_bridge_check``. Pinning a fixed
    # signature here would silently drop any newly added kwargs and
    # break Responses-API routing for callers that need it.
    def _guarded_bridge_check(*args: Any, **kwargs: Any) -> tuple:
        model_info, out_model = _original(*args, **kwargs)
        if _force_chat_completions and model_info.get("mode") == "responses":
            model_info["mode"] = "chat"
        return model_info, out_model

    _litellm_main.responses_api_bridge_check = _guarded_bridge_check
    _responses_api_guard_installed = True


def _activate_chat_completions_fallback(
    reason: str,
    *,
    model: str | None = None,
    tag: str = "",
    proactive: bool = False,
) -> None:
    """Activate process-wide Chat Completions fallback.

    Sets the sticky ``_force_chat_completions`` flag, installs the
    bridge-check guard (idempotent), and emits a single user-facing
    message per run via ``_responses_api_fallback_warned``.

    When ``proactive`` is True (env-var seed at import time, or an
    explicit API preference) the message is logged at INFO and omits
    the "set ASSERT_PREFER_CHAT_COMPLETIONS=1" hint — the user has
    already opted in. When False (reactive recovery from a region
    error) it is logged at WARN with the hint.
    """
    global _force_chat_completions, _responses_api_fallback_warned
    _force_chat_completions = True
    _install_responses_api_guard()
    if _responses_api_fallback_warned:
        return
    _responses_api_fallback_warned = True
    prefix = f"{model}{tag}: " if model else ""
    if proactive:
        log.info(
            "%sUsing Azure Chat Completions instead of Responses API "
            "for this run (%s).",
            prefix, reason,
        )
    else:
        log.warning(
            "%sFalling back from Azure Responses API to Chat Completions "
            "for the remainder of this run (%s). Set "
            "ASSERT_PREFER_CHAT_COMPLETIONS=1 to skip this round-trip "
            "upfront in unsupported regions.",
            prefix, reason,
        )


def _apply_chat_completions_preference() -> None:
    """Public entry for proactive activation (kept for back-compat)."""
    _activate_chat_completions_fallback("preference set via API", proactive=True)


def _drop_web_search_for_fallback(
    options: "GenerateOptions", model: str, *, reason: str
) -> "GenerateOptions":
    """Disable ``web_search`` on ``options`` and warn once per run.

    ``web_search`` is implemented via the Responses API
    ``web_search_preview`` tool — there is no Chat Completions
    equivalent on Azure. When the Responses API is unavailable in the
    target region (or the Chat-Completions fallback is already active),
    we degrade gracefully: the call still succeeds but without web
    grounding. The first occurrence is logged loudly so the user knows
    the run produced different output than a Responses-API-supporting
    region would have.
    """
    global _web_search_drop_warned
    if not _web_search_drop_warned:
        _web_search_drop_warned = True
        tag = f" [{options.call_label}]" if options.call_label else ""
        log.warning(
            "%s%s: dropping web_search and routing via Chat Completions "
            "(%s). Web grounding is disabled for the remainder of this run.",
            model, tag, reason,
        )
    return replace(options, web_search=False)


def _classify_llm_error(exc: Exception) -> Exception:
    """Wrap litellm exceptions into categorized errors.

    LiteLLM maps provider HTTP errors to OpenAI-compatible exception types.
    We re-classify them into four categories that drive retry behaviour in
    ``_with_retries``.  The mapping below is based on the LiteLLM exception
    table (https://docs.litellm.ai/docs/exception_mapping).

    HTTP   LiteLLM exception                       → ASSERT class (retried?)
    ─────  ──────────────────────────────────────── ─────────────────────────
    400    BadRequestError                          → LLMInputError  (no)
           ├─ ContextWindowExceededError            → LLMInputError  (no)
           ├─ ContentPolicyViolationError           → LLMInputError  (no)
           ├─ UnsupportedParamsError                → LLMInputError  (no)
           └─ ImageFetchError                       → LLMInputError  (no)
    401    AuthenticationError                      → LLMAuthError   (no)
    403    PermissionDeniedError                    → LLMAuthError   (no)
    404    NotFoundError                            → LLMInputError  (no)
    408    Timeout  (inherits APIConnectionError)   → LLMProviderError (yes)
    422    UnprocessableEntityError                 → LLMInputError  (no)
    429    RateLimitError                           → LLMRateLimitError (yes, coordinated)
    500    APIError / APIConnectionError            → LLMProviderError (yes)
    503    ServiceUnavailableError (inherits APIError) → LLMProviderError (yes)
    ≥500   InternalServerError (inherits APIError)  → LLMProviderError (yes)
    N/A    APIResponseValidationError               → LLMInputError  (no)
    N/A    BudgetExceededError                      → (falls through as-is)

    Check order matters: specific subclasses must be tested before their
    bases (e.g. NotFoundError before APIError, since NotFoundError inherits
    from APIStatusError → APIError on some providers).
    """
    litellm = _get_litellm_module()
    # 401 — bad credentials
    if isinstance(exc, litellm.AuthenticationError):
        msg = f"Authentication failed: {exc}"
        if _AZURE_AUTH_MODE in {"aad", "aad-fallback"}:
            if _AZURE_AAD_DEP_MISSING:
                msg += (
                    "\nNo AZURE_API_KEY is set and azure-identity is not "
                    "installed. Either export AZURE_API_KEY, or install "
                    "Azure AD support with "
                    "`pip install 'assert-ai[azure-aad]'`."
                )
            else:
                msg += (
                    "\nAzure AD auth is active. Verify the caller identity "
                    "has the 'Cognitive Services OpenAI User' role on the "
                    "Azure OpenAI resource, or set AZURE_API_KEY to fall "
                    "back to API-key auth."
                )
        err = LLMAuthError(msg)
        err.__cause__ = exc
        return err
    # 403 — insufficient permissions
    if isinstance(exc, getattr(litellm, "PermissionDeniedError", ())):
        err = LLMAuthError(f"Permission denied: {exc}")
        err.__cause__ = exc
        return err
    # 429 — rate limited (retryable with coordinated backoff)
    if isinstance(exc, litellm.RateLimitError):
        err = LLMRateLimitError(f"Rate limited: {exc}")
        err.__cause__ = exc
        return err
    # ── Responses API region fallback (must precede BadRequestError) ──
    # Azure OpenAI rejects Responses API requests in unsupported regions
    # (West Europe etc.) with one of these observed messages:
    #   - "API version not supported"            (HTTP 400 → BadRequestError)
    #   - "responses api is not enabled"         (HTTP 404 → NotFoundError/APIError)
    # We check before the BadRequestError / NotFoundError / APIError handlers
    # so the error routes to the Chat-Completions fallback in
    # ``_with_retries`` instead of being recorded as an unrecoverable bad
    # request.
    #
    # Important: do NOT gate on ``_force_chat_completions`` here. At high
    # concurrency the first task to hit this marker activates fallback and
    # installs the bridge-check patch, but other tasks already in-flight
    # against the Responses API will surface the same marker shortly
    # afterwards. Gating the check on ``_force_chat_completions`` caused
    # those in-flight failures to fall through to the NotFoundError /
    # BadRequestError handlers, get classified as ``LLMInputError``, and
    # propagate without ever being retried on the Chat path. Per-task
    # loop prevention is handled inside ``_with_retries`` via a
    # ``chat_fallback_attempts`` counter.
    _msg_lower = str(exc).lower()
    if any(
        marker in _msg_lower
        for marker in (
            "responses api is not enabled",
            "api version not supported",
        )
    ):
        err = _ResponsesApiNotAvailableError(
            f"Azure Responses API not available: {exc}"
        )
        err.__cause__ = exc
        return err
    # 400 — bad request (includes ContextWindowExceeded, ContentPolicyViolation)
    if isinstance(exc, litellm.BadRequestError):
        # ContentPolicyViolationError is a BadRequestError subclass on most
        # providers; some providers (notably Azure OpenAI) instead surface
        # the content filter via a generic BadRequestError whose message
        # contains a stable marker. We classify both paths as a
        # LLMContentFilterError subclass so adversarial-eval workloads can
        # tolerate them per-row.
        #
        # Observed Azure / OpenAI variants (each can appear independently):
        #   - "content_filter" / "content filter"
        #   - "ResponsibleAIPolicyViolation"
        #   - "high-risk cyber activity" / "potentially high-risk"
        #   - "your prompt was flagged" + "usage policy"
        #   - "Invalid prompt: ..."  (OpenAI reasoning-models guard text)
        cf_cls = getattr(litellm, "ContentPolicyViolationError", None)
        msg_text = str(exc)
        msg_lower = msg_text.lower()
        is_content_filter = (
            cf_cls is not None and isinstance(exc, cf_cls)
        ) or any(
            marker in msg_lower
            for marker in (
                "content_filter",
                "content filter",
                "responsibleaipolicyviolation",
                "high-risk cyber activity",
                "potentially high-risk",
                "flagged as potentially violating",
                "violating our usage policy",
                "prompt was flagged",
                "invalid prompt:",
            )
        )
        if is_content_filter:
            err = LLMContentFilterError(f"Content filtered: {exc}")
        else:
            err = LLMInputError(f"Bad request: {exc}")
        err.__cause__ = exc
        return err
    # 404 — model/deployment not found
    if isinstance(exc, litellm.NotFoundError):
        err = LLMInputError(f"Model/deployment not found: {exc}")
        err.__cause__ = exc
        return err
    # 422 — unprocessable entity
    if isinstance(exc, getattr(litellm, "UnprocessableEntityError", ())):
        err = LLMInputError(f"Unprocessable entity: {exc}")
        err.__cause__ = exc
        return err
    # N/A — response schema validation failure
    if isinstance(exc, getattr(litellm, "APIResponseValidationError", ())):
        err = LLMInputError(f"Response validation failed: {exc}")
        err.__cause__ = exc
        return err
    # 500/503/≥500/timeout/connection — retryable provider errors
    # This is the catch-all for APIError, APIConnectionError,
    # InternalServerError, ServiceUnavailableError, and Timeout
    # (all inherit from APIError or APIConnectionError).
    if isinstance(exc, (litellm.APIError, litellm.APIConnectionError)):
        err = LLMProviderError(f"Provider error: {exc}")
        err.__cause__ = exc
        return err
    return exc


def _first_choice(raw_response: Any) -> Any:
    choices = _get_value(raw_response, "choices")
    if isinstance(choices, Sequence) and not isinstance(choices, (str, bytes)) and choices:
        return choices[0]
    return None


def _normalize_usage(raw_usage: Any) -> UsageStats | None:
    if raw_usage is None:
        return None
    # Chat Completions API uses prompt_tokens/completion_tokens;
    # Responses API uses input_tokens/output_tokens.
    prompt = _coerce_int(_get_value(raw_usage, "prompt_tokens")) or _coerce_int(_get_value(raw_usage, "input_tokens"))
    completion = _coerce_int(_get_value(raw_usage, "completion_tokens")) or _coerce_int(_get_value(raw_usage, "output_tokens"))
    total = _coerce_int(_get_value(raw_usage, "total_tokens"))
    if total is None and prompt is not None and completion is not None:
        total = prompt + completion

    # Prompt-cache accounting. OpenAI/Azure expose cached prompt tokens
    # under {prompt,input}_tokens_details.cached_tokens (Chat Completions
    # vs Responses API). Anthropic exposes them as top-level
    # cache_read_input_tokens / cache_creation_input_tokens. LiteLLM
    # passes both shapes through unchanged.
    cached_input = _coerce_int(_get_value(raw_usage, "cache_read_input_tokens"))
    if cached_input is None:
        prompt_details = (
            _get_value(raw_usage, "prompt_tokens_details")
            or _get_value(raw_usage, "input_tokens_details")
        )
        if prompt_details is not None:
            cached_input = _coerce_int(_get_value(prompt_details, "cached_tokens"))
    cache_creation = _coerce_int(_get_value(raw_usage, "cache_creation_input_tokens"))

    # Diagnose providers that return a usage object but with all zero/None
    # token counts. Seen with truncated Azure Responses API calls (status
    # 'incomplete'): the accumulator records 1 call but reports 0 in / 0 out,
    # which masks the real token spend. Log at debug so we can attribute
    # mystery zero-token rows in the future without spamming normal runs.
    if not prompt and not completion and not total:
        log.debug(
            "Usage payload contained zero/None tokens (raw=%r) -- "
            "provider likely returned an incomplete or error response",
            raw_usage,
        )

    return UsageStats(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        cached_input_tokens=cached_input,
        cache_creation_input_tokens=cache_creation,
        raw=raw_usage,
    )


def _extract_text_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, Sequence) and not isinstance(content, (str, bytes)):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            text_value = _get_value(item, "text")
            if isinstance(text_value, str):
                parts.append(text_value)
                continue
            nested_text = _get_value(_get_value(item, "text"), "value")
            if isinstance(nested_text, str):
                parts.append(nested_text)
        return "".join(parts)
    return str(content)


def _extract_responses_output_text(output_items: Any) -> str:
    if not isinstance(output_items, Sequence) or isinstance(output_items, (str, bytes)):
        return ""

    parts: list[str] = []
    for item in output_items:
        if _get_value(item, "type") != "message":
            continue
        for content_item in _get_value(item, "content") or []:
            if _get_value(content_item, "type") != "output_text":
                continue
            text_value = _get_value(content_item, "text")
            if isinstance(text_value, str):
                parts.append(text_value)
    return "".join(parts)


def _maybe_parse_json(raw_text: str) -> Any:
    if not raw_text:
        return None
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        return None


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _get_value(obj: Any, key: str) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key)
    return getattr(obj, key, None)


# ── Public API ─────────────────────────────────────────────────


def _log_response(label: str, model: str, response: "ModelResponse", elapsed: float, **extra: object) -> None:
    """Log a compact one-line summary of an LLM call at DEBUG level."""
    usage = response.usage
    if usage and usage.prompt_tokens is not None:
        tokens = f"{usage.prompt_tokens}+{usage.completion_tokens or 0} tokens"
        if usage.cached_input_tokens:
            tokens = f"{tokens} ({usage.cached_input_tokens} cached)"
    else:
        tokens = "? tokens"
    parts = [f"model={model}"]
    for key, value in extra.items():
        parts.append(f"{key}={value}")
    parts.extend([f"{elapsed:.1f}s", tokens, f"finish={response.finish_reason or '?'}"])
    log.debug(f"{label}: {', '.join(parts)}")

__all__ = [
    "build_llm_call_trace",
    "GenerateOptions",
    "Message",
    "MessageLike",
    "ModelResponse",
    "is_truncated_response",
    "summarize_response",
    "ToolCall",
    "ToolCallLike",
    "UsageAccumulator",
    "UsageStats",
    "generate",
    "generate_structured",
    "generate_with_tools",
    "to_jsonable",
    "track_usage",
    "LLMAuthError",
    "LLMInputError",
    "LLMRateLimitError",
    "LLMProviderError",
]


# ── Per-model adaptive rate limiter ────────────────────────────

_MAX_RETRIES = 5
_INITIAL_BACKOFF_S = 1.0
_MAX_BACKOFF_S = 120.0
_DEFAULT_COOLDOWN_S = 2.0
# Number of consecutive successful calls (per model) required before
# halving the escalated base cooldown back toward _DEFAULT_COOLDOWN_S.
# Higher values are more conservative — the limiter "remembers" past
# saturation longer, which avoids the oscillation pattern where a single
# success after a 429 storm reverts the base to the default and we
# immediately re-escalate on the next burst.
_DECAY_AFTER_SUCCESSES = 10


def _extract_retry_after(exc: Exception) -> float | None:
    """Try to extract Retry-After seconds from a rate limit error."""
    for source in (exc, getattr(exc, "__cause__", None)):
        if source is None:
            continue
        for attr in ("headers", "response_headers"):
            headers = getattr(source, attr, None)
            if not headers:
                continue
            value = headers.get("Retry-After") or headers.get("retry-after")
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    pass
    return None


class _ModelRateLimiter:
    """Coordinated per-model cooldown.

    When *any* task receives a 429 for a model, a cooldown timestamp is
    set.  All tasks calling that model wait until the cooldown expires
    before issuing the next request, preventing the thundering-herd
    pattern where N concurrent tasks all retry into the same wall.

    To avoid a *stampede at cooldown expiry* (all waiters resuming at
    the same instant), ``wait_if_cooled`` adds per-task jitter that
    spreads wake-ups over the cooldown window.

    Escalation logic: only escalate the base cooldown when a 429 arrives
    **after** the previous cooldown has fully expired — meaning the
    cooldown was too short.  Concurrent 429s that arrive while a cooldown
    is already active are from in-flight requests sent before the cooldown
    was set, so they just extend the existing cooldown without escalating.

    Decay logic: an escalated base does **not** revert to the default on
    a single successful call.  Instead, ``report_success`` counts
    consecutive successes per model and only halves the base after
    ``_DECAY_AFTER_SUCCESSES`` clean calls in a row, with a floor at
    ``_DEFAULT_COOLDOWN_S``.  This prevents the "amnesia" pattern where
    one success between bursts wipes out everything we learned about how
    saturated the deployment is — without it, the base oscillates
    between default and 2x default forever instead of climbing to a
    sustainable rate.  Any 429 resets the counter.
    """

    def __init__(self) -> None:
        self._cooldown_until: dict[str, float] = {}
        self._base_cooldown: dict[str, float] = {}
        self._consecutive_successes: dict[str, int] = {}
        self._lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def wait_if_cooled(self, model: str) -> None:
        """Block until the cooldown for *model* has expired, with jitter."""
        until = self._cooldown_until.get(model, 0.0)
        delay = until - time.monotonic()
        if delay > 0:
            # Spread wake-ups: each task adds random jitter so they
            # don't all resume at the exact cooldown expiry.
            jitter = random.uniform(0, max(delay * 0.5, 0.5))
            total = delay + jitter
            log.info("Rate limiter: waiting %.1fs (%.1fs + %.1fs jitter) for model %s cooldown", total, delay, jitter, model)
            await asyncio.sleep(total)

    async def report_rate_limit(
        self, model: str, retry_after: float | None = None,
    ) -> bool:
        """Record a 429 and set or extend a cooldown for *model*.

        Returns ``True`` if this call initiated or escalated the cooldown
        (the caller should log at WARNING level).  Returns ``False`` for
        concurrent in-flight 429s during an active cooldown — these are
        absorbed silently to avoid log spam.

        Escalation only happens when the previous cooldown already
        expired — this means we retried and *still* got 429, so the
        base cooldown was too short.  Concurrent in-flight 429s during
        an active cooldown are absorbed without escalating.
        """
        now = time.monotonic()
        async with self._get_lock():
            current_until = self._cooldown_until.get(model, 0.0)
            base = self._base_cooldown.get(model, _DEFAULT_COOLDOWN_S)

            if retry_after is not None:
                # Server told us how long — trust it and adopt as base.
                wait_s = min(retry_after, _MAX_BACKOFF_S)
                self._base_cooldown[model] = wait_s
                is_new = True
                source = "Retry-After"
            elif current_until <= now:
                # Cooldown expired and we still got 429 → escalate base.
                wait_s = min(base * 2, _MAX_BACKOFF_S)
                self._base_cooldown[model] = wait_s
                is_new = True
                source = "escalation"
            else:
                # Active cooldown — concurrent in-flight request, don't
                # escalate.  Just extend if needed using current base.
                wait_s = base
                is_new = False
                source = "active"

            new_until = now + wait_s
            if new_until > current_until:
                self._cooldown_until[model] = new_until
                if is_new:
                    log.warning(
                        "Rate limiter: model %s cooled down for %.1fs (%s)",
                        model, wait_s, source,
                    )
            # Any 429 — escalation, server-directed, or absorbed in-flight —
            # invalidates the run of clean successes used to decay the base.
            self._consecutive_successes[model] = 0
            return is_new

    def report_success(self, model: str) -> None:
        """Decay the escalated base cooldown after sustained success.

        The base is only halved once ``_DECAY_AFTER_SUCCESSES`` consecutive
        successful calls accumulate without an intervening 429, and the
        result is floored at ``_DEFAULT_COOLDOWN_S``.  Models that have
        never been escalated are no-ops.
        """
        # Nothing to decay if we never escalated above the default.
        current = self._base_cooldown.get(model)
        if current is None or current <= _DEFAULT_COOLDOWN_S:
            # Keep the counter clean so a future escalation starts fresh.
            self._consecutive_successes.pop(model, None)
            return

        n = self._consecutive_successes.get(model, 0) + 1
        if n < _DECAY_AFTER_SUCCESSES:
            self._consecutive_successes[model] = n
            return

        new_base = max(current / 2, _DEFAULT_COOLDOWN_S)
        if new_base <= _DEFAULT_COOLDOWN_S:
            # Fully decayed back to default — drop the entry so the next
            # 429 escalates from _DEFAULT_COOLDOWN_S * 2 like the first.
            self._base_cooldown.pop(model, None)
        else:
            self._base_cooldown[model] = new_base
        self._consecutive_successes[model] = 0
        log.info(
            "Rate limiter: model %s base cooldown decayed %.1fs → %.1fs after %d successes",
            model, current, new_base, _DECAY_AFTER_SUCCESSES,
        )


_rate_limiter = _ModelRateLimiter()


async def _with_retries(call_fn: Any, *, model: str, label: str | None = None) -> Any:
    """Retry an async LLM call with coordinated backoff on retryable errors.

    On ``LLMRateLimitError`` the global per-model cooldown is set and
    all concurrent tasks for that model pause via ``wait_if_cooled``.
    The individual per-attempt exponential backoff is only applied for
    non-rate-limit retryable errors (e.g. 5xx); for 429s the
    coordinated cooldown (with jitter) is the sole wait mechanism,
    preventing the stampede-at-expiry pattern.

    Retry budget is tracked **per error class against this task only**:
    waiting through another task's cooldown does *not* consume any of
    this task's retry budget. This matters at high concurrency, where
    the previous "every iteration counts" model would burn a request's
    entire budget on cooldowns set by other tasks before it ever got a
    real chance to call the API.
    """
    tag = f" [{label}]" if label else ""
    last_exc: Exception | None = None
    own_429s = 0
    own_5xx = 0
    # Per-task Chat-Completions fallback budget. We allow exactly ONE
    # demote-and-retry per task: the first ``_ResponsesApiNotAvailableError``
    # triggers the (possibly global) fallback activation and re-tries on
    # the Chat path. A second occurrence within the same task means the
    # retry itself failed with the same marker — that genuinely means
    # the Chat path is misconfigured (e.g. wrong api-version) and we
    # must surface the error instead of looping forever.
    chat_fallback_attempts = 0
    # Hard ceiling on total iterations to guarantee termination even
    # if other tasks keep refreshing the cooldown indefinitely. With
    # _MAX_RETRIES=5 this allows up to 24 cooldown waits without ever
    # making a real attempt before bailing out — far more headroom than
    # any sane scenario needs.
    safety_iterations_cap = (_MAX_RETRIES + 1) * 4
    iterations = 0
    while True:
        iterations += 1
        if iterations > safety_iterations_cap:
            log.error(
                "%s%s exceeded safety iteration cap (%d) in retry loop; giving up",
                model, tag, safety_iterations_cap,
            )
            if last_exc is not None:
                raise last_exc
            raise RuntimeError(
                f"_with_retries safety cap exceeded for {model} without ever observing a call result"
            )
        await _rate_limiter.wait_if_cooled(model)
        try:
            result = await call_fn()
            _rate_limiter.report_success(model)
            return result
        except Exception as exc:
            classified = _classify_llm_error(exc)
            last_exc = classified
            if isinstance(classified, LLMRateLimitError):
                own_429s += 1
                attempts_used = own_429s
                attempts_total = _MAX_RETRIES + 1
                if own_429s > _MAX_RETRIES:
                    raise classified from exc
                retry_after = _extract_retry_after(exc)
                is_new = await _rate_limiter.report_rate_limit(model, retry_after)
                if is_new:
                    if retry_after is not None:
                        log.warning(
                            "%s%s (attempt %d/%d), honoring server Retry-After=%.1fs: %s",
                            model, tag, attempts_used, attempts_total, retry_after, classified,
                        )
                    else:
                        log.warning(
                            "%s%s (attempt %d/%d), waiting for coordinated cooldown: %s",
                            model, tag, attempts_used, attempts_total, classified,
                        )
                else:
                    log.debug(
                        "%s%s (attempt %d/%d), in-flight 429 during active cooldown",
                        model, tag, attempts_used, attempts_total,
                    )
                # Skip individual backoff — the coordinated cooldown
                # (checked at loop top) handles the wait with jitter.
                continue
            # ── Responses API fallback (must precede generic LLMProviderError) ──
            # Azure returns a region-specific "Responses API is not
            # enabled" / "API version not supported" error that will
            # never succeed on retry while the model is still routed
            # through the Responses bridge. ``_ResponsesApiNotAvailableError``
            # is a subclass of ``LLMProviderError``, so this branch must
            # run before the generic provider-error branch, otherwise the
            # request is burned through standard 5xx retries and the
            # Chat-Completions fallback never fires.
            #
            # We allow ONE Chat-path retry per task (``chat_fallback_attempts``).
            # The global ``_force_chat_completions`` flag may already be True
            # because a concurrent task tripped the marker first — that's
            # fine, this task still needs its one retry on the Chat path.
            # If the retry itself surfaces the same marker, the Chat path
            # is genuinely broken (e.g. wrong api-version) and we
            # re-raise instead of looping forever.
            if isinstance(classified, _ResponsesApiNotAvailableError):
                if chat_fallback_attempts >= 1:
                    raise classified from exc
                chat_fallback_attempts += 1
                if not _force_chat_completions:
                    _activate_chat_completions_fallback(
                        "Azure Responses API not enabled in region",
                        model=model, tag=tag,
                    )
                continue
            if isinstance(classified, LLMProviderError):
                own_5xx += 1
                attempts_used = own_5xx
                attempts_total = _MAX_RETRIES + 1
                if own_5xx > _MAX_RETRIES:
                    raise classified from exc
                # Non-rate-limit retryable error (5xx): individual backoff.
                backoff = min(_INITIAL_BACKOFF_S * (2 ** (own_5xx - 1)), _MAX_BACKOFF_S)
                jitter = random.uniform(0, backoff * 0.5)
                wait_s = backoff + jitter
                log.warning(
                    "%s%s (attempt %d/%d), retrying in %.1fs: %s",
                    model, tag, attempts_used, attempts_total, wait_s, classified,
                )
                await asyncio.sleep(wait_s)
                continue
            raise classified from exc


async def generate(
    model: str,
    messages: str | Sequence[MessageLike],
    options: GenerateOptions | None = None,
) -> ModelResponse:
    """Run a standard async text generation call."""
    resolved_options = options or GenerateOptions()
    # Proactive degradation: if the Chat-Completions fallback is already
    # active for this run (another task tripped it, or the user opted in
    # via ASSERT_PREFER_CHAT_COMPLETIONS), drop web_search up front —
    # there is no Chat Completions equivalent for web grounding.
    if _force_chat_completions and resolved_options.web_search:
        resolved_options = _drop_web_search_for_fallback(
            resolved_options, model,
            reason="Chat Completions fallback is active",
        )
    api_mode = "responses" if resolved_options.web_search else "chat_completion"
    if resolved_options.web_search:
        _require_web_search_preview_support(model)
    litellm = _get_litellm_module()
    t0 = time.monotonic()

    if resolved_options.web_search:
        payload = _build_responses_payload(model, messages, resolved_options)
        payload["tools"] = [{"type": "web_search_preview"}]
        responses_client, is_async = _responses_client(litellm)

        async def _call() -> Any:
            if is_async:
                return await _await_with_timeout(
                    responses_client(**payload),
                    timeout_s=resolved_options.timeout_s,
                )
            return await _run_sync_with_timeout(
                responses_client,
                timeout_s=resolved_options.timeout_s,
                **payload,
            )
    else:
        payload = _build_chat_payload(model, messages, resolved_options)

        async def _call() -> Any:
            return await _await_with_timeout(
                litellm.acompletion(**payload),
                timeout_s=resolved_options.timeout_s,
            )

    try:
        raw_response = await _with_retries(_call, model=model, label=resolved_options.call_label)
    except _ResponsesApiNotAvailableError:
        # Reactive degradation: this task was the first to trip the
        # Responses-API-not-available marker. ``_with_retries`` already
        # activated the global fallback but had no way to rebuild the
        # closure without the web_search tool. Re-issue the call here
        # via Chat Completions without web grounding.
        if not resolved_options.web_search:
            raise
        return await generate(
            model, messages,
            options=_drop_web_search_for_fallback(
                resolved_options, model,
                reason="Responses API not available in this region",
            ),
        )
    result = normalize_response(
        raw_response,
        api_mode="responses" if resolved_options.web_search else "chat_completion",
        request_payload=payload,
    )
    _log_response("generate", model, result, time.monotonic() - t0, api_mode=api_mode)
    _record_usage(result.usage, model=model)
    return result


async def generate_structured(
    model: str,
    messages: str | Sequence[MessageLike],
    *,
    schema_name: str,
    json_schema: dict[str, Any],
    options: GenerateOptions | None = None,
) -> ModelResponse:
    """Run a structured generation call constrained by a JSON schema."""
    resolved_options = options or GenerateOptions()
    # Proactive degradation: see ``generate`` for the rationale.
    if _force_chat_completions and resolved_options.web_search:
        resolved_options = _drop_web_search_for_fallback(
            resolved_options, model,
            reason="Chat Completions fallback is active",
        )
    api_mode = "responses" if resolved_options.web_search else "chat_completion"
    if resolved_options.web_search:
        _require_web_search_preview_support(model)
    litellm = _get_litellm_module()
    t0 = time.monotonic()

    if resolved_options.web_search:
        payload = _build_responses_payload(model, messages, resolved_options)
        payload["tools"] = [{"type": "web_search_preview"}]
        payload["text"] = {
            "format": build_json_schema_text_format(
                schema_name,
                json_schema,
            )
        }
        responses_client, is_async = _responses_client(litellm)

        async def _call() -> Any:
            if is_async:
                return await _await_with_timeout(
                    responses_client(**payload),
                    timeout_s=resolved_options.timeout_s,
                )
            return await _run_sync_with_timeout(
                responses_client,
                timeout_s=resolved_options.timeout_s,
                **payload,
            )
    else:
        payload = _build_chat_payload(model, messages, resolved_options)
        payload["response_format"] = build_json_schema_response_format(
            schema_name,
            json_schema,
        )

        async def _call() -> Any:
            return await _await_with_timeout(
                litellm.acompletion(**payload),
                timeout_s=resolved_options.timeout_s,
            )

    try:
        raw_response = await _with_retries(_call, model=model, label=resolved_options.call_label)
    except _ResponsesApiNotAvailableError:
        # Reactive degradation: see ``generate`` for the rationale.
        if not resolved_options.web_search:
            raise
        return await generate_structured(
            model, messages,
            schema_name=schema_name, json_schema=json_schema,
            options=_drop_web_search_for_fallback(
                resolved_options, model,
                reason="Responses API not available in this region",
            ),
        )
    result = normalize_response(
        raw_response,
        api_mode="responses" if resolved_options.web_search else "chat_completion",
        request_payload=payload,
    )
    _log_response("generate_structured", model, result, time.monotonic() - t0, api_mode=api_mode, schema=schema_name)
    _record_usage(result.usage, model=model)
    return result


async def generate_with_tools(
    model: str,
    messages: str | Sequence[MessageLike],
    *,
    tools: list[dict[str, Any]],
    options: GenerateOptions | None = None,
) -> ModelResponse:
    """Run a tool-capable chat completion."""
    resolved_options = options or GenerateOptions()
    t0 = time.monotonic()
    payload = _build_chat_payload(model, messages, resolved_options)
    payload["tools"] = tools
    if resolved_options.tool_choice is not None:
        payload["tool_choice"] = resolved_options.tool_choice

    litellm = _get_litellm_module()

    async def _call() -> Any:
        return await _await_with_timeout(
            litellm.acompletion(**payload),
            timeout_s=resolved_options.timeout_s,
        )

    raw_response = await _with_retries(_call, model=model, label=resolved_options.call_label)
    result = normalize_response(
        raw_response,
        api_mode="chat_completion",
        request_payload=payload,
    )
    _log_response("generate_with_tools", model, result, time.monotonic() - t0, tools=len(tools))
    _record_usage(result.usage, model=model)
    return result


# ── Import-time AZURE_API_BASE normalization ───────────────────
# LiteLLM appends the OpenAI-API path itself (``/openai/deployments/…``
# or ``/openai/v1/responses``), so AZURE_API_BASE must be the bare
# account endpoint. A trailing ``/openai/...`` or ``/openai/v1/...``
# suffix (commonly copy-pasted from the Azure portal's "Endpoint"
# field for the Responses API) causes LiteLLM to build malformed URLs
# like ``…/openai/v1/responses/openai/deployments/…``, which surface as
# "Resource not found" or "api-version query parameter is not allowed
# when using /v1 path". Strip any such suffix defensively at import
# time so users don't have to debug it themselves.
def _normalize_azure_api_base() -> None:
    raw = os.environ.get("AZURE_API_BASE", "").strip()
    if not raw:
        return
    cleaned = raw.rstrip("/")
    lowered = cleaned.lower()
    # Find the leftmost ``/openai`` segment and drop it + everything
    # after it. This handles ``/openai``, ``/openai/v1``,
    # ``/openai/v1/responses``, ``/openai/deployments/...``, etc.
    idx = lowered.find("/openai")
    if idx > 0:  # skip when ``/openai`` is part of the host (it shouldn't be)
        cleaned = cleaned[:idx]
    # LiteLLM is happiest with a trailing slash on the base URL.
    normalized = cleaned.rstrip("/") + "/"
    if normalized != raw:
        log.info(
            "AZURE_API_BASE normalized: stripped path suffix so LiteLLM "
            "can build the correct deployment URL.",
        )
        os.environ["AZURE_API_BASE"] = normalized


_normalize_azure_api_base()


# Pick the Azure OpenAI auth mode once for the process. Pre-warming the
# credential here means the first request never pays the
# DefaultAzureCredential construction cost and any missing-dep error
# surfaces at import time when the user has explicitly opted into AAD
# (rather than from inside the first request).
def _configure_azure_auth_mode() -> None:
    global _AZURE_AUTH_MODE, _AZURE_AAD_DEP_MISSING
    _AZURE_AUTH_MODE = azure_auth.resolve_azure_auth_mode()
    if _AZURE_AUTH_MODE == "aad":
        log.info(
            "Azure OpenAI auth mode: AAD (forced via %s).",
            azure_auth.ENV_USE_AAD_FLAG,
        )
        if azure_auth.get_azure_token_provider() is None:
            _AZURE_AAD_DEP_MISSING = True
            log.warning(
                "%s is set but azure-identity is not installed; the next "
                "azure/* request will fail. Install with: "
                "pip install 'assert-ai[azure-aad]'",
                azure_auth.ENV_USE_AAD_FLAG,
            )
    elif _AZURE_AUTH_MODE == "aad-fallback":
        # Stay quiet for users who haven't configured Azure yet — the
        # actionable error only matters when an azure/* request fires.
        log.debug(
            "Azure OpenAI auth mode: AAD fallback (no AZURE_API_KEY set).",
        )
        if azure_auth.get_azure_token_provider() is None:
            _AZURE_AAD_DEP_MISSING = True
    else:
        log.info("Azure OpenAI auth mode: API key (AZURE_API_KEY).")


_configure_azure_auth_mode()


# ── Import-time env-var seed ───────────────────────────────────
# Users in Azure regions known to lack Responses API support
# (e.g. West Europe at time of writing) can pre-arm the fallback
# by exporting ``ASSERT_PREFER_CHAT_COMPLETIONS=1``. This avoids
# the one wasted Responses API round-trip + the user-visible WARN
# on every cold start, while keeping the reactive fallback as a
# safety net for regions that lose support later.
if os.environ.get("ASSERT_PREFER_CHAT_COMPLETIONS", "").strip().lower() in ("1", "true", "yes"):
    _activate_chat_completions_fallback(
        "ASSERT_PREFER_CHAT_COMPLETIONS env var set",
        proactive=True,
    )
