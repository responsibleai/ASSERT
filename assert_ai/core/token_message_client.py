# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Shared message rendering and response extraction for token transports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence


@dataclass(slots=True)
class TokenMessageCompletion:
    text: str
    reasoning: str
    finish_reason: str | None
    raw: dict[str, Any]


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, Sequence) and not isinstance(content, (str, bytes, bytearray)):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, Mapping) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts)
    return str(content or "")


def _to_message(message: Mapping[str, Any], chat: Any, *, transport: str) -> Any:
    role = str(message.get("role") or "user")
    content = _content_text(message.get("content"))
    if role == "system":
        return chat.Message.system(content)
    if role == "user":
        return chat.Message.user(content)
    if role == "assistant":
        return chat.Message.assistant(content, channel="final")
    raise ValueError(f"{transport} model transport does not support message role '{role}'")


def _channel_name(message: Any) -> str | None:
    channel = getattr(message, "channel", None)
    value = getattr(channel, "value", channel)
    return value if isinstance(value, str) else None


def _strip_final_prefix(text: str) -> str:
    normalized = text.strip()
    lowered = normalized.lower()
    if lowered.startswith("final:"):
        return normalized[len("final:") :].strip()
    if lowered.startswith("channel: final"):
        parts = normalized.split("\n", 1)
        return parts[1].strip() if len(parts) > 1 else ""
    return normalized


async def complete_messages(
    messages: Sequence[Mapping[str, Any]],
    *,
    token_completer_config: Any,
    renderer: str,
    top_p: float,
    max_tokens: int | None,
    temperature: float | None,
    transport: str,
    chat: Any,
    render_content: Callable[[Any], str],
    message_completer_type: Any,
) -> TokenMessageCompletion:
    completion_params: dict[str, Any] = {"top_p": top_p}
    if max_tokens is not None:
        completion_params["max_tokens"] = max_tokens
    if temperature is not None:
        completion_params["temperature"] = temperature
    completer = message_completer_type.Config(
        token_completer_config=token_completer_config,
        renderer=renderer,
        completion_params=completion_params,
    ).build()
    try:
        conversation = chat.Conversation(
            messages=[_to_message(message, chat, transport=transport) for message in messages],
        )
        completion = await completer.async_completion([conversation])
    finally:
        completer.close()

    if not completion.choices:
        raise RuntimeError(f"{transport} completion returned no choices")
    choice = completion.choices[0]
    if choice.error is not None:
        raise RuntimeError(f"{transport} completion failed: {choice.error}")
    output_messages = list(choice.get_messages())
    if not output_messages:
        raise RuntimeError(f"{transport} completion returned no messages")

    rendered = [
        {
            "channel": _channel_name(message),
            "content": render_content(message).strip(),
        }
        for message in output_messages
    ]
    final_messages = [item["content"] for item in rendered if item["channel"] == "final"]
    analysis_messages = [item["content"] for item in rendered if item["channel"] == "analysis"]
    text = _strip_final_prefix(final_messages[-1] if final_messages else rendered[-1]["content"])
    if not text:
        raise RuntimeError(f"{transport} completion returned an empty response")
    return TokenMessageCompletion(
        text=text,
        reasoning="\n".join(part for part in analysis_messages if part),
        finish_reason=getattr(choice, "finish_reason", None) or "stop",
        raw={"messages": rendered},
    )
