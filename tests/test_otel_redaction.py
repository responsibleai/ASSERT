"""Tests for credential redaction on the OTel span -> transcript event path."""

from __future__ import annotations

from unittest.mock import patch

from assert_ai.core.otel import _redact_events


def _event_with(content: str) -> dict:
    return {
        "actor": "target",
        "edit": {
            "type": "add_message",
            "message": {"role": "assistant", "content": content},
        },
    }


def _content(event: dict) -> str:
    return event["edit"]["message"]["content"]


class TestSpanRedaction:
    def test_credentials_in_message_content_are_redacted(self):
        events = _redact_events([_event_with("token is sk-abcdefghijklmnopqrstuv")])
        assert "sk-abcdefghijklmnopqrstuv" not in _content(events[0])

    def test_credentials_in_tool_arguments_are_redacted(self):
        events = _redact_events([
            {
                "edit": {
                    "type": "tool_call",
                    "arguments": {"headers": "Authorization: Bearer abc.def.ghi"},
                }
            }
        ])
        assert "abc.def.ghi" not in str(events[0])

    def test_nested_structures_are_traversed(self):
        events = _redact_events([
            {"a": [{"b": {"c": ["password=hunter2"]}}]}
        ])
        assert "hunter2" not in str(events[0])

    def test_ordinary_evaluation_content_is_unchanged(self):
        text = "The assistant declined and explained the policy in detail."
        events = _redact_events([_event_with(text)])
        assert _content(events[0]) == text

    def test_non_string_values_are_preserved(self):
        events = _redact_events([{"tokens": 42, "ok": True, "none": None}])
        assert events[0] == {"tokens": 42, "ok": True, "none": None}

    def test_redaction_can_be_disabled(self):
        raw = "sk-abcdefghijklmnopqrstuv"
        with patch.dict("os.environ", {"ASSERT_REDACT_SPAN_CONTENT": "0"}):
            events = _redact_events([_event_with(raw)])
        assert _content(events[0]) == raw

    def test_empty_event_list(self):
        assert _redact_events([]) == []
