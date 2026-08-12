"""Tests for whole-run consumption ceilings.

Every other limit in ASSERT is per-call or per-task, so nothing bounded a run as
a whole. The realistic failure is a typo - sample_size: 5000 against an
expensive judge - with nothing able to stop it once it starts.
"""

from __future__ import annotations

import pytest

from assert_ai.config import parse_run_limits
from assert_ai.core.config_model import RunLimits
from assert_ai.core.model_client import (
    BudgetExceededError,
    UsageAccumulator,
    UsageStats,
)


def _usage(prompt: int = 10, completion: int = 5) -> UsageStats:
    return UsageStats(prompt_tokens=prompt, completion_tokens=completion)


class TestParseRunLimits:
    def test_missing_block_is_inactive(self):
        limits = parse_run_limits(None)
        assert limits.is_active() is False
        assert limits.on_exceed == "stop"

    def test_empty_block_is_inactive(self):
        assert parse_run_limits({}).is_active() is False

    def test_parses_all_fields(self):
        limits = parse_run_limits(
            {
                "max_total_calls": 100,
                "max_total_tokens": 5000,
                "max_wall_time_s": 60,
                "on_exceed": "warn",
            }
        )
        assert limits.max_total_calls == 100
        assert limits.max_total_tokens == 5000
        assert limits.max_wall_time_s == 60.0
        assert limits.on_exceed == "warn"
        assert limits.is_active() is True

    def test_rejects_unknown_key(self):
        with pytest.raises(ValueError, match="unsupported field"):
            parse_run_limits({"max_cost": 10})

    @pytest.mark.parametrize("value", [0, -1, "10", True, 1.5])
    def test_rejects_non_positive_int(self, value):
        with pytest.raises(ValueError, match="positive integer"):
            parse_run_limits({"max_total_calls": value})

    @pytest.mark.parametrize("value", [0, -5, "60", True])
    def test_rejects_bad_wall_time(self, value):
        with pytest.raises(ValueError, match="positive number"):
            parse_run_limits({"max_wall_time_s": value})

    def test_rejects_bad_on_exceed(self):
        with pytest.raises(ValueError, match="'stop' or 'warn'"):
            parse_run_limits({"on_exceed": "abort"})

    def test_rejects_non_mapping(self):
        with pytest.raises(ValueError, match="must be a mapping"):
            parse_run_limits([1, 2, 3])


class TestLimitEnforcement:
    def test_no_limits_never_raises(self):
        acc = UsageAccumulator()
        for _ in range(50):
            acc.add(_usage())
        assert acc.calls == 50

    def test_call_ceiling_stops(self):
        acc = UsageAccumulator(limits=RunLimits(max_total_calls=3))
        for _ in range(3):
            acc.add(_usage())
        with pytest.raises(BudgetExceededError, match="max_total_calls"):
            acc.add(_usage())

    def test_token_ceiling_stops(self):
        acc = UsageAccumulator(limits=RunLimits(max_total_tokens=30))
        acc.add(_usage(10, 5))
        acc.add(_usage(10, 5))
        with pytest.raises(BudgetExceededError, match="max_total_tokens"):
            acc.add(_usage(10, 5))

    def test_wall_time_ceiling_stops(self):
        acc = UsageAccumulator(
            limits=RunLimits(max_wall_time_s=0.0001), started_at=0.0
        )
        with pytest.raises(BudgetExceededError, match="max_wall_time_s"):
            acc.add(_usage())

    def test_limits_apply_across_stages_not_per_stage(self):
        """A per-stage accumulator must still see the whole run's consumption."""
        limits = RunLimits(max_total_calls=5)
        first = UsageAccumulator(limits=limits)
        for _ in range(5):
            first.add(_usage())

        second = UsageAccumulator(limits=limits, baseline_calls=first.calls)
        with pytest.raises(BudgetExceededError, match="max_total_calls"):
            second.add(_usage())

    def test_warn_mode_continues_and_warns_once(self, caplog):
        acc = UsageAccumulator(
            limits=RunLimits(max_total_calls=1, on_exceed="warn")
        )
        with caplog.at_level("WARNING"):
            for _ in range(4):
                acc.add(_usage())
        assert acc.calls == 4
        assert caplog.text.count("Run limit exceeded") == 1

    def test_exceeded_reports_first_breach_only(self):
        acc = UsageAccumulator(
            limits=RunLimits(max_total_calls=1, max_total_tokens=1, on_exceed="warn")
        )
        acc.add(_usage())
        acc.add(_usage())
        assert "max_total_calls" in acc.exceeded()

    def test_inactive_limits_object_is_ignored(self):
        acc = UsageAccumulator(limits=RunLimits())
        for _ in range(20):
            acc.add(_usage())
        assert acc.exceeded() is None


class TestBudgetErrorClassification:
    def test_not_an_llm_error(self):
        """Retry and fallback paths must not treat a ceiling as a provider fault."""
        from assert_ai.core.model_client import (
            LLMProviderError,
            LLMRateLimitError,
        )

        assert not issubclass(BudgetExceededError, LLMProviderError)
        assert not issubclass(BudgetExceededError, LLMRateLimitError)
