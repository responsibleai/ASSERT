import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from assert_ai.core.llm_diagnostics import write_llm_failure_diagnostic
from assert_ai.core.model_client import ModelResponse, UsageStats


class LLMFailureDiagnosticTest(unittest.TestCase):
    def test_writes_full_response_metadata_and_sanitized_request(self) -> None:
        full_text = "malformed-response-" * 80
        response = ModelResponse(
            text=full_text,
            finish_reason="stop",
            status="completed",
            incomplete_details={"reason": "provider-specific-detail"},
            model="azure/gpt-5.4",
            response_id="resp-123",
            usage=UsageStats(prompt_tokens=40, completion_tokens=60, total_tokens=100),
            api_mode="responses",
            request_payload={"api_key": "secret", "model": "azure/gpt-5.4"},
            raw={"output_text": full_text, "status": "completed"},
        )

        with TemporaryDirectory() as tmp_dir:
            path = write_llm_failure_diagnostic(
                response,
                diagnostics_dir=Path(tmp_dir),
                stage="systematization",
                reason="unparseable_output",
                attempt=1,
            )

            self.assertIsNotNone(path)
            assert path is not None
            payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["stage"], "systematization")
        self.assertEqual(payload["reason"], "unparseable_output")
        self.assertEqual(payload["attempt"], 1)
        self.assertEqual(payload["response_metadata"]["finish_reason"], "stop")
        self.assertEqual(payload["response_metadata"]["status"], "completed")
        self.assertEqual(
            payload["response_metadata"]["incomplete_details"],
            {"reason": "provider-specific-detail"},
        )
        self.assertEqual(payload["llm_call"]["request"]["api_key"], "[REDACTED]")
        self.assertEqual(payload["llm_call"]["derived"]["content"], full_text)
        self.assertEqual(payload["llm_call"]["response"]["output_text"], full_text)

    def test_write_failure_returns_none_instead_of_masking_original_error(self) -> None:
        response = ModelResponse(text="bad output", model="azure/gpt-5.4")
        with (
            TemporaryDirectory() as tmp_dir,
            patch("assert_ai.core.llm_diagnostics.write_json", side_effect=OSError("disk full")),
        ):
            path = write_llm_failure_diagnostic(
                response,
                diagnostics_dir=Path(tmp_dir),
                stage="systematization",
                reason="unparseable_output",
            )

        self.assertIsNone(path)


if __name__ == "__main__":
    unittest.main()