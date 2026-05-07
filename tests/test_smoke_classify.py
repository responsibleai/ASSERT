"""Tests for the smoke-cell classifier.

Validates the per-classification routing in `scripts.smoke_classify._classify`.
Kept minimal — the classifier is small and pattern-based; we just want to
catch obvious regressions in the routing rules.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

# scripts/ is not on sys.path by default; load the module directly.
_CLASSIFY_PATH = Path(__file__).resolve().parents[1] / "scripts" / "smoke_classify.py"
_spec = importlib.util.spec_from_file_location("smoke_classify", _CLASSIFY_PATH)
assert _spec and _spec.loader, "could not load smoke_classify.py"
smoke_classify = importlib.util.module_from_spec(_spec)
sys.modules["smoke_classify"] = smoke_classify
_spec.loader.exec_module(smoke_classify)


def _result(label: str = "test", exit_code: int = 0, n_seeds: int | None = 3,
            n_scores: int | None = 0, elapsed: float = 1.0) -> dict:
    return {
        "label": label,
        "exit_code": exit_code,
        "elapsed_s": elapsed,
        "n_seeds": n_seeds,
        "n_scores": n_scores,
    }


class ClassifierTest(unittest.TestCase):
    def test_pass_when_clean_exit_with_scores(self):
        cls = smoke_classify._classify(_result(exit_code=0, n_scores=3), log_text="")
        self.assertEqual(cls.classification, "PASS")
        self.assertFalse(cls.blocking)

    def test_rate_limit_classified_external_warn(self):
        log = "litellm.RateLimitError: 429 Too Many Requests\nat openai/gpt-5.4-mini"
        cls = smoke_classify._classify(_result(exit_code=1, n_scores=0), log_text=log)
        self.assertEqual(cls.classification, "EXTERNAL_RATE_LIMIT")
        self.assertFalse(cls.blocking)

    def test_5xx_classified_external_warn(self):
        log = "azure.core.exceptions.ServiceResponseError: 503 Service Unavailable"
        cls = smoke_classify._classify(_result(exit_code=1, n_scores=0), log_text=log)
        self.assertEqual(cls.classification, "EXTERNAL_5XX")
        self.assertFalse(cls.blocking)

    def test_network_error_classified_external_warn(self):
        log = "aiohttp.ClientConnectorError: Cannot connect to host openai.azure.com"
        cls = smoke_classify._classify(_result(exit_code=1, n_scores=0), log_text=log)
        self.assertEqual(cls.classification, "EXTERNAL_NETWORK")
        self.assertFalse(cls.blocking)

    def test_content_filter_classified_external_warn(self):
        log = "ResponsibleAIPolicyViolation: prompt blocked by content filter"
        cls = smoke_classify._classify(_result(exit_code=1, n_scores=0), log_text=log)
        self.assertEqual(cls.classification, "EXTERNAL_CONTENT_FILTER")
        self.assertFalse(cls.blocking)

    def test_p2m_frame_in_traceback_classified_p2m_bug(self):
        log = (
            'Traceback (most recent call last):\n'
            '  File "/repo/p2m/stages/rollout.py", line 700, in _run\n'
            '    raise ValueError("oops")\n'
            'ValueError: oops\n'
        )
        cls = smoke_classify._classify(_result(exit_code=1, n_scores=0), log_text=log)
        self.assertEqual(cls.classification, "P2M_BUG")
        self.assertTrue(cls.blocking)

    def test_zero_exit_but_no_scores_blocked_as_p2m_bug(self):
        cls = smoke_classify._classify(_result(exit_code=0, n_scores=0), log_text="")
        self.assertEqual(cls.classification, "P2M_BUG")
        self.assertTrue(cls.blocking)

    def test_unknown_failure_blocked_conservatively(self):
        cls = smoke_classify._classify(
            _result(exit_code=1, n_scores=0),
            log_text="some unrelated stderr noise",
        )
        self.assertEqual(cls.classification, "UNKNOWN")
        self.assertTrue(cls.blocking)

    def test_external_pattern_takes_precedence_over_p2m_frame(self):
        # If a transient surfaces a p2m frame in the traceback, the external
        # classification should still win (rate-limits sometimes bubble
        # through retry wrappers in p2m/core/model_client.py).
        log = (
            'litellm.RateLimitError: 429 Too Many Requests\n'
            'Traceback:\n'
            '  File "/repo/p2m/core/model_client.py", line 200, in _retry\n'
            '    raise\n'
        )
        cls = smoke_classify._classify(_result(exit_code=1, n_scores=0), log_text=log)
        self.assertEqual(cls.classification, "EXTERNAL_RATE_LIMIT")
        self.assertFalse(cls.blocking)


if __name__ == "__main__":
    unittest.main()
