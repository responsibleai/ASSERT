"""LiteLLM-backed model helpers for the measurements pipeline."""

from __future__ import annotations

import asyncio
import importlib
import json
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Mapping, Sequence

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
    """Wrap litellm exceptions into categorized errors."""
    litellm = _get_litellm_module()
    if isinstance(exc, litellm.AuthenticationError):
        err = LLMAuthError(f"Authentication failed: {exc}")
        err.__cause__ = exc
        return err
    if isinstance(exc, litellm.RateLimitError):
        err = LLMRateLimitError(f"Rate limited: {exc}")
        err.__cause__ = exc
        return err
    if isinstance(exc, litellm.BadRequestError):
        err = LLMInputError(f"Bad request: {exc}")
        err.__cause__ = exc
        return err
    if isinstance(exc, litellm.NotFoundError):
        err = LLMInputError(f"Model/deployment not found: {exc}")
        err.__cause__ = exc
        return err
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


async def generate(
    model: str,
    messages: str | Sequence[MessageLike],
    options: GenerateOptions | None = None,
) -> ModelResponse:
    """Run a standard async text generation call."""
    resolved_options = options or GenerateOptions()
    litellm = _get_litellm_module()
    try:
        if resolved_options.web_search:
            payload = _build_responses_payload(model, messages, resolved_options)
            payload["tools"] = [{"type": "web_search_preview"}]
            responses_client, is_async = _responses_client(litellm)
            if is_async:
                raw_response = await _await_with_timeout(
                    responses_client(**payload),
                    timeout_s=resolved_options.timeout_s,
                )
            else:
                raw_response = await _run_sync_with_timeout(
                    responses_client,
                    timeout_s=resolved_options.timeout_s,
                    **payload,
                )
        else:
            payload = _build_chat_payload(model, messages, resolved_options)
            raw_response = await _await_with_timeout(
                litellm.acompletion(**payload),
                timeout_s=resolved_options.timeout_s,
            )
    except Exception as exc:
        raise _classify_llm_error(exc) from exc
    return normalize_response(
        raw_response,
        api_mode="responses" if resolved_options.web_search else "chat_completion",
        request_payload=payload,
    )


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
    try:
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
            if is_async:
                raw_response = await _await_with_timeout(
                    responses_client(**payload),
                    timeout_s=resolved_options.timeout_s,
                )
            else:
                raw_response = await _run_sync_with_timeout(
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
            raw_response = await _await_with_timeout(
                litellm.acompletion(**payload),
                timeout_s=resolved_options.timeout_s,
            )
    except Exception as exc:
        raise _classify_llm_error(exc) from exc
    return normalize_response(
        raw_response,
        api_mode="responses" if resolved_options.web_search else "chat_completion",
        request_payload=payload,
    )


async def generate_with_tools(
    model: str,
    messages: str | Sequence[MessageLike],
    *,
    tools: list[dict[str, Any]],
    options: GenerateOptions | None = None,
) -> ModelResponse:
    """Run a tool-capable chat completion."""
    resolved_options = options or GenerateOptions()
    payload = _build_chat_payload(model, messages, resolved_options)
    payload["tools"] = tools
    if resolved_options.tool_choice is not None:
        payload["tool_choice"] = resolved_options.tool_choice

    litellm = _get_litellm_module()
    try:
        raw_response = await _await_with_timeout(
            litellm.acompletion(**payload),
            timeout_s=resolved_options.timeout_s,
        )
    except Exception as exc:
        raise _classify_llm_error(exc) from exc
    return normalize_response(
        raw_response,
        api_mode="chat_completion",
        request_payload=payload,
    )
