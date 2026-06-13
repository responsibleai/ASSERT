# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Language-model adapters for ACS policy generation."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - imported only for annotations
    from acs_generator import LanguageModel


_DEFAULT_ASSERT_MODEL = "azure/gpt-5.4"
_VALID_KINDS = ("assert", "openai-compatible", "fake")


class AssertLanguageModel:
    """ASSERT LiteLLM-backed adapter for the ACS generator protocol."""

    def __init__(
        self,
        model: str,
        *,
        temperature: float = 0.0,
        response_format_json: bool = True,
    ) -> None:
        if not model or not model.strip():
            raise ValueError("AssertLanguageModel requires a non-empty model name")
        self.model = model
        self.temperature = temperature
        self.response_format_json = response_format_json

    def complete(self, system: str, user: str) -> str:
        """Return the raw assistant text for the ACS generator's JSON plan prompt."""
        litellm = _assert_litellm_module()
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
        }
        if self.response_format_json:
            payload["response_format"] = {"type": "json_object"}

        try:
            response = litellm.completion(**payload)
        except Exception as exc:
            if not self.response_format_json or not _is_response_format_rejection(exc):
                raise
            fallback_payload = dict(payload)
            fallback_payload.pop("response_format", None)
            response = litellm.completion(**fallback_payload)

        return _extract_message_content(response)


def build_language_model(
    kind: str = "assert",
    *,
    model: str | None = None,
    responses: list | None = None,
) -> "LanguageModel":
    """Build a language model compatible with ``acs_generator.GenerationEngine``."""
    normalized_kind = kind.strip().lower()
    if normalized_kind == "assert":
        return AssertLanguageModel(model=model or _DEFAULT_ASSERT_MODEL)

    if normalized_kind not in _VALID_KINDS:
        valid = ", ".join(_VALID_KINDS)
        raise ValueError(f"Unknown ACS language model kind '{kind}'. Valid kinds: {valid}")

    acs_generator = _import_acs_generator()
    if normalized_kind == "openai-compatible":
        return acs_generator.OpenAICompatibleLanguageModel(model=model)
    if responses is None or len(responses) == 0:
        raise ValueError("build_language_model(kind='fake') requires at least one response")
    return acs_generator.FakeLanguageModel(responses)


def _assert_litellm_module() -> Any:
    from assert_ai.core import model_client

    return model_client._get_litellm_module()


def _import_acs_generator() -> Any:
    try:
        import acs_generator
    except ModuleNotFoundError as exc:
        if exc.name == "acs_generator":
            raise ModuleNotFoundError(
                'ACS policy generation requires the ACS extra. Install it with: pip install "assert-ai[acs]"',
                name=exc.name,
            ) from exc
        raise
    return acs_generator


def _is_response_format_rejection(exc: Exception) -> bool:
    message = str(exc).lower()
    response_format_markers = ("response_format", "json_object", "json mode", "json schema")
    rejection_markers = (
        "unsupported",
        "not support",
        "not supported",
        "invalid",
        "unrecognized",
        "unknown",
        "unexpected",
        "not allowed",
        "does not support",
    )
    return any(marker in message for marker in response_format_markers) and any(
        marker in message for marker in rejection_markers
    )


def _extract_message_content(response: Any) -> str:
    choices = _get_value(response, "choices")
    if choices:
        first_choice = choices[0]
        message = _get_value(first_choice, "message")
        content = _get_value(message, "content") if message is not None else None
        if content is not None:
            return _content_to_text(content)

    output_text = _get_value(response, "output_text")
    if output_text is not None:
        return _content_to_text(output_text)

    raise RuntimeError("LiteLLM response did not include assistant message content")


def _get_value(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        if parts:
            return "\n".join(parts)
    if isinstance(content, (dict, list)):
        return json.dumps(content)
    return str(content)


__all__ = ["AssertLanguageModel", "build_language_model"]
