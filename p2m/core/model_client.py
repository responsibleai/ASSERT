"""LiteLLM-backed model helpers for the measurements pipeline."""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import random
import time
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Mapping, Sequence

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
    """Normalized token accounting."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    raw: Any = None


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
        payload["usage"] = {
            "input_tokens": response.usage.prompt_tokens,
            "output_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
        }
    if response.model:
        payload["model"] = response.model
    if response.response_id:
        payload["response_id"] = response.response_id
    return payload


def build_llm_call_trace(response: ModelResponse, *, source: str) -> dict[str, Any]:
    """Build an artifact-safe owned LLM call trace."""
    return {
        "source": source,
        "api_mode": response.api_mode or "",
        "request": to_jsonable(response.request_payload or {}),
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
                "litellm is not installed. Run `uv sync` in omni/measurements "
                "before using p2m.core.model_client."
            ) from exc
        # Silence noisy litellm warnings that pollute stderr
        _LITELLM_MODULE.suppress_debug_info = True
        # Disable LiteLLM's internal retry so _with_retries is the
        # sole retry layer — avoids double-retry and lets the
        # coordinated per-model cooldown work correctly.
        _LITELLM_MODULE.num_retries = 0
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

class LLMRateLimitError(Exception):
    """Rate limited — retryable after backoff."""

class LLMProviderError(Exception):
    """Provider-side error (5xx) — may be retryable."""


def _classify_llm_error(exc: Exception) -> Exception:
    """Wrap litellm exceptions into categorized errors.

    LiteLLM maps provider HTTP errors to OpenAI-compatible exception types.
    We re-classify them into four categories that drive retry behaviour in
    ``_with_retries``.  The mapping below is based on the LiteLLM exception
    table (https://docs.litellm.ai/docs/exception_mapping).

    HTTP   LiteLLM exception                       → p2m class (retried?)
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
        err = LLMAuthError(f"Authentication failed: {exc}")
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
    # 400 — bad request (includes ContextWindowExceeded, ContentPolicyViolation)
    if isinstance(exc, litellm.BadRequestError):
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
    return UsageStats(
        prompt_tokens=_coerce_int(_get_value(raw_usage, "prompt_tokens")),
        completion_tokens=_coerce_int(_get_value(raw_usage, "completion_tokens")),
        total_tokens=_coerce_int(_get_value(raw_usage, "total_tokens")),
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
    "summarize_response",
    "ToolCall",
    "ToolCallLike",
    "UsageStats",
    "generate",
    "generate_structured",
    "generate_with_tools",
    "to_jsonable",
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
    On any successful call, the escalation level resets.
    """

    def __init__(self) -> None:
        self._cooldown_until: dict[str, float] = {}
        self._base_cooldown: dict[str, float] = {}
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
            elif current_until <= now:
                # Cooldown expired and we still got 429 → escalate base.
                wait_s = min(base * 2, _MAX_BACKOFF_S)
                self._base_cooldown[model] = wait_s
                is_new = True
            else:
                # Active cooldown — concurrent in-flight request, don't
                # escalate.  Just extend if needed using current base.
                wait_s = base
                is_new = False

            new_until = now + wait_s
            if new_until > current_until:
                self._cooldown_until[model] = new_until
                if is_new:
                    log.warning(
                        "Rate limiter: model %s cooled down for %.1fs", model, wait_s,
                    )
            return is_new

    def report_success(self, model: str) -> None:
        """Reset escalation state after a successful call."""
        self._base_cooldown.pop(model, None)


_rate_limiter = _ModelRateLimiter()


async def _with_retries(call_fn: Any, *, model: str, label: str | None = None) -> Any:
    """Retry an async LLM call with coordinated backoff on retryable errors.

    On ``LLMRateLimitError`` the global per-model cooldown is set and
    all concurrent tasks for that model pause via ``wait_if_cooled``.
    The individual per-attempt exponential backoff is only applied for
    non-rate-limit retryable errors (e.g. 5xx); for 429s the
    coordinated cooldown (with jitter) is the sole wait mechanism,
    preventing the stampede-at-expiry pattern.
    """
    tag = f" [{label}]" if label else ""
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        await _rate_limiter.wait_if_cooled(model)
        try:
            result = await call_fn()
            _rate_limiter.report_success(model)
            return result
        except Exception as exc:
            classified = _classify_llm_error(exc)
            if attempt < _MAX_RETRIES and isinstance(classified, (LLMRateLimitError, LLMProviderError)):
                last_exc = classified
                if isinstance(classified, LLMRateLimitError):
                    retry_after = _extract_retry_after(exc)
                    is_new = await _rate_limiter.report_rate_limit(model, retry_after)
                    if is_new:
                        log.warning(
                            "%s%s (attempt %d/%d), waiting for coordinated cooldown: %s",
                            model, tag, attempt + 1, _MAX_RETRIES + 1, classified,
                        )
                    else:
                        log.debug(
                            "%s%s (attempt %d/%d), in-flight 429 during active cooldown",
                            model, tag, attempt + 1, _MAX_RETRIES + 1,
                        )
                    # Skip individual backoff — the coordinated cooldown
                    # (checked at loop top) handles the wait with jitter.
                    continue
                # Non-rate-limit retryable error (5xx): individual backoff.
                backoff = min(_INITIAL_BACKOFF_S * (2 ** attempt), _MAX_BACKOFF_S)
                jitter = random.uniform(0, backoff * 0.5)
                wait_s = backoff + jitter
                log.warning(
                    "%s%s (attempt %d/%d), retrying in %.1fs: %s",
                    model, tag, attempt + 1, _MAX_RETRIES + 1, wait_s, classified,
                )
                await asyncio.sleep(wait_s)
                continue
            raise classified from exc
    assert last_exc is not None
    raise last_exc


async def generate(
    model: str,
    messages: str | Sequence[MessageLike],
    options: GenerateOptions | None = None,
) -> ModelResponse:
    """Run a standard async text generation call."""
    resolved_options = options or GenerateOptions()
    litellm = _get_litellm_module()
    api_mode = "responses" if resolved_options.web_search else "chat_completion"
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

    raw_response = await _with_retries(_call, model=model, label=resolved_options.call_label)
    result = normalize_response(
        raw_response,
        api_mode="responses" if resolved_options.web_search else "chat_completion",
        request_payload=payload,
    )
    _log_response("generate", model, result, time.monotonic() - t0, api_mode=api_mode)
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
    litellm = _get_litellm_module()
    api_mode = "responses" if resolved_options.web_search else "chat_completion"
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

    raw_response = await _with_retries(_call, model=model, label=resolved_options.call_label)
    result = normalize_response(
        raw_response,
        api_mode="responses" if resolved_options.web_search else "chat_completion",
        request_payload=payload,
    )
    _log_response("generate_structured", model, result, time.monotonic() - t0, api_mode=api_mode, schema=schema_name)
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
    return result
