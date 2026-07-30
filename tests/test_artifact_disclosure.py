"""Regression tests for information disclosure in persisted artifacts.

The transcript produced by the inference stage is written to
``inference_set.jsonl`` and rendered by the viewer, so anything placed in it is
disclosed to everyone who can read a run directory.
"""

from __future__ import annotations

import traceback
from unittest.mock import patch

from assert_ai.core.security import env_flag, redact_text, sanitize_payload


def _formatted_traceback() -> str:
    try:
        raise RuntimeError("connect failed for postgres://user:hunter2@db.internal/prod")
    except RuntimeError:
        return traceback.format_exc()


class TestEnvFlag:
    def test_truthy_values(self):
        for value in ("1", "true", "TRUE", "yes", "Yes"):
            with patch.dict("os.environ", {"ASSERT_TEST_FLAG": value}):
                assert env_flag("ASSERT_TEST_FLAG") is True

    def test_falsy_and_unset_values(self):
        for value in ("", "0", "false", "no", "maybe"):
            with patch.dict("os.environ", {"ASSERT_TEST_FLAG": value}):
                assert env_flag("ASSERT_TEST_FLAG") is False
        with patch.dict("os.environ", {}, clear=True):
            assert env_flag("ASSERT_TEST_FLAG") is False


class TestTracebackDisclosure:
    def test_traceback_text_contains_sensitive_detail(self):
        """Establishes what the transcript would leak if the trace were included."""
        tb = _formatted_traceback()
        assert "hunter2" in tb
        assert "File \"" in tb

    def test_transcript_content_default_excludes_traceback(self):
        """Mirrors the default branch in _run_turns: summary only."""
        tb = _formatted_traceback()
        exc = RuntimeError("connect failed")
        content = f"[TARGET ERROR: {type(exc).__name__}: {exc}]"
        with patch.dict("os.environ", {}, clear=True):
            if env_flag("ASSERT_TRANSCRIPT_TRACEBACKS"):
                content += "\n" + tb
        assert "Traceback" not in content
        assert "hunter2" not in content
        # The judge still learns that the target failed, and how.
        assert "RuntimeError" in content
        assert "connect failed" in content

    def test_opt_in_traceback_is_redacted(self):
        tb = _formatted_traceback()
        cleaned = redact_text(tb)
        assert "hunter2" not in cleaned
        assert "[REDACTED]" in cleaned


class TestRedactText:
    def test_sanitize_payload_does_not_redact_free_text(self):
        """Documents why redact_text exists: key-based redaction misses inline secrets."""
        text = "api_key=sk-abcdefghijklmnopqrst"
        assert sanitize_payload({"traceback": text})["traceback"] == text

    def test_redacts_key_value_assignments(self):
        for text in (
            "api_key=sk-abcdefghijklmnopqrst",
            'password: "hunter2"',
            "client_secret = abc123def456",
            '"access_token": "xyz789"',
        ):
            out = redact_text(text)
            assert "[REDACTED]" in out, text

    def test_redacts_url_credentials(self):
        out = redact_text("postgres://user:hunter2@db.internal/prod")
        assert "hunter2" not in out
        assert "db.internal" in out

    def test_redacts_auth_headers(self):
        assert "abc.def" not in redact_text("Authorization: Bearer abc.def")

    def test_redacts_known_token_shapes(self):
        for token in (
            "sk-abcdefghijklmnopqrstuvwx",
            "AKIAIOSFODNN7EXAMPLE",
            "ghp_abcdefghijklmnopqrstuvwxyz012345",
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc123",
        ):
            assert token not in redact_text(f"value is {token} here")

    def test_preserves_ordinary_text(self):
        text = "The assistant refused to provide instructions for the task."
        assert redact_text(text) == text

    def test_handles_empty(self):
        assert redact_text("") == ""
