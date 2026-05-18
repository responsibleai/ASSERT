"""Tests for the per-model adaptive rate limiter and retry logic."""

import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from p2m.core.model_client import (
    LLMProviderError,
    LLMRateLimitError,
    _DECAY_AFTER_SUCCESSES,
    _DEFAULT_COOLDOWN_S,
    _extract_retry_after,
    _ModelRateLimiter,
    _with_retries,
)


# ── _extract_retry_after ──────────────────────────────────────


class ExtractRetryAfterTest(unittest.TestCase):
    def test_returns_none_for_plain_exception(self) -> None:
        self.assertIsNone(_extract_retry_after(Exception("boom")))

    def test_extracts_from_headers_attribute(self) -> None:
        exc = Exception("429")
        exc.headers = {"Retry-After": "3.5"}  # type: ignore[attr-defined]
        self.assertEqual(_extract_retry_after(exc), 3.5)

    def test_extracts_from_response_headers_attribute(self) -> None:
        exc = Exception("429")
        exc.response_headers = {"retry-after": "10"}  # type: ignore[attr-defined]
        self.assertEqual(_extract_retry_after(exc), 10.0)

    def test_extracts_from_cause_chain(self) -> None:
        inner = Exception("inner")
        inner.headers = {"Retry-After": "7"}  # type: ignore[attr-defined]
        outer = Exception("outer")
        outer.__cause__ = inner
        self.assertEqual(_extract_retry_after(outer), 7.0)

    def test_returns_none_for_non_numeric_value(self) -> None:
        exc = Exception("429")
        exc.headers = {"Retry-After": "not-a-number"}  # type: ignore[attr-defined]
        self.assertIsNone(_extract_retry_after(exc))


# ── _ModelRateLimiter ─────────────────────────────────────────


class ModelRateLimiterTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.limiter = _ModelRateLimiter()

    async def test_wait_if_cooled_returns_immediately_when_no_cooldown(self) -> None:
        """No cooldown set → should not sleep."""
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await self.limiter.wait_if_cooled("model-a")
            mock_sleep.assert_not_called()

    async def test_report_rate_limit_sets_cooldown(self) -> None:
        is_new = await self.limiter.report_rate_limit("model-a")
        self.assertTrue(is_new)
        # Cooldown should now be set in the future
        until = self.limiter._cooldown_until.get("model-a", 0.0)
        self.assertGreater(until, time.monotonic())

    async def test_report_rate_limit_with_retry_after_uses_server_value(self) -> None:
        await self.limiter.report_rate_limit("model-a", retry_after=15.0)
        self.assertEqual(self.limiter._base_cooldown["model-a"], 15.0)

    async def test_retry_after_clamped_to_max(self) -> None:
        await self.limiter.report_rate_limit("model-a", retry_after=999.0)
        # Should be clamped to _MAX_BACKOFF_S (120)
        self.assertEqual(self.limiter._base_cooldown["model-a"], 120.0)

    async def test_escalation_doubles_base_on_expired_cooldown(self) -> None:
        # First 429 → sets base to _DEFAULT_COOLDOWN_S * 2 = 4.0
        await self.limiter.report_rate_limit("model-a")
        first_base = self.limiter._base_cooldown["model-a"]

        # Simulate cooldown expiring
        self.limiter._cooldown_until["model-a"] = time.monotonic() - 1

        # Second 429 after expiry → should escalate
        await self.limiter.report_rate_limit("model-a")
        second_base = self.limiter._base_cooldown["model-a"]
        self.assertEqual(second_base, min(first_base * 2, 60.0))

    async def test_concurrent_429_during_active_cooldown_does_not_escalate(self) -> None:
        await self.limiter.report_rate_limit("model-a")
        base_after_first = self.limiter._base_cooldown["model-a"]

        # Cooldown still active → concurrent in-flight 429
        is_new = await self.limiter.report_rate_limit("model-a")
        self.assertFalse(is_new)
        # Base should not have changed
        self.assertEqual(self.limiter._base_cooldown["model-a"], base_after_first)

    async def test_report_success_does_not_reset_base_on_first_success(self) -> None:
        """Sticky escalation: a single success after a 429 must NOT revert the base.

        This is the key fix for the oscillation pattern at high concurrency:
        when 19 sibling tasks wake from a coordinated cooldown and 18 succeed,
        the *first* success used to instantly pop the escalated base. The next
        429 then re-escalated from the default, never climbing past 2x default.
        """
        await self.limiter.report_rate_limit("model-a")
        escalated = self.limiter._base_cooldown["model-a"]
        self.assertGreater(escalated, _DEFAULT_COOLDOWN_S)
        self.limiter.report_success("model-a")
        # Base must still be elevated; only the success-streak counter advanced.
        self.assertEqual(self.limiter._base_cooldown["model-a"], escalated)
        self.assertEqual(self.limiter._consecutive_successes["model-a"], 1)

    async def test_report_success_decays_base_after_threshold(self) -> None:
        """After _DECAY_AFTER_SUCCESSES clean calls, the base halves."""
        # Escalate twice: default(2) → 4 → 8.
        await self.limiter.report_rate_limit("model-a")
        self.limiter._cooldown_until["model-a"] = time.monotonic() - 1
        await self.limiter.report_rate_limit("model-a")
        escalated = self.limiter._base_cooldown["model-a"]
        self.assertEqual(escalated, _DEFAULT_COOLDOWN_S * 4)

        for _ in range(_DECAY_AFTER_SUCCESSES - 1):
            self.limiter.report_success("model-a")
        # Still elevated until the Kth success.
        self.assertEqual(self.limiter._base_cooldown["model-a"], escalated)

        self.limiter.report_success("model-a")
        # Halved.
        self.assertEqual(
            self.limiter._base_cooldown["model-a"], escalated / 2,
        )
        # Counter reset so the *next* halving requires another K successes.
        self.assertEqual(self.limiter._consecutive_successes["model-a"], 0)

    async def test_report_success_decay_floors_at_default_and_drops_entry(self) -> None:
        """Decay below _DEFAULT_COOLDOWN_S clears the per-model base entry.

        Dropping the entry means the next 429 escalates from the default
        again (default * 2), which is the desired starting state.
        """
        await self.limiter.report_rate_limit("model-a")  # 2 → 4
        # Decay 4 → 2 (floored at default → entry cleared).
        for _ in range(_DECAY_AFTER_SUCCESSES):
            self.limiter.report_success("model-a")
        self.assertNotIn("model-a", self.limiter._base_cooldown)
        # Counter reset on decay.
        self.assertEqual(self.limiter._consecutive_successes.get("model-a", 0), 0)

    async def test_report_rate_limit_resets_consecutive_success_counter(self) -> None:
        """Any 429 must invalidate the streak so we don't decay mid-storm."""
        await self.limiter.report_rate_limit("model-a")
        for _ in range(_DECAY_AFTER_SUCCESSES - 1):
            self.limiter.report_success("model-a")
        self.assertEqual(
            self.limiter._consecutive_successes["model-a"], _DECAY_AFTER_SUCCESSES - 1,
        )
        self.limiter._cooldown_until["model-a"] = time.monotonic() - 1
        await self.limiter.report_rate_limit("model-a")
        self.assertEqual(self.limiter._consecutive_successes["model-a"], 0)

    async def test_report_success_is_noop_when_never_escalated(self) -> None:
        """A model that has never seen a 429 is left alone."""
        self.limiter.report_success("model-a")
        self.assertNotIn("model-a", self.limiter._base_cooldown)
        self.assertNotIn("model-a", self.limiter._consecutive_successes)

    async def test_models_are_independent(self) -> None:
        await self.limiter.report_rate_limit("model-a")
        self.assertIn("model-a", self.limiter._cooldown_until)
        self.assertNotIn("model-b", self.limiter._cooldown_until)

    async def test_wait_if_cooled_sleeps_when_cooldown_active(self) -> None:
        self.limiter._cooldown_until["model-a"] = time.monotonic() + 5.0
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            with patch("random.uniform", return_value=0.1):
                await self.limiter.wait_if_cooled("model-a")
                mock_sleep.assert_called_once()
                slept = mock_sleep.call_args[0][0]
                self.assertGreater(slept, 0)


# ── _with_retries ─────────────────────────────────────────────


def _make_litellm_module():
    """Build a fake litellm module with exception classes."""
    ns = SimpleNamespace()
    ns.AuthenticationError = type("AuthenticationError", (Exception,), {})
    ns.RateLimitError = type("RateLimitError", (Exception,), {})
    ns.BadRequestError = type("BadRequestError", (Exception,), {})
    ns.NotFoundError = type("NotFoundError", (Exception,), {})
    ns.APIError = type("APIError", (Exception,), {})
    ns.APIConnectionError = type("APIConnectionError", (Exception,), {})
    return ns


class WithRetriesTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        # Reset the global rate limiter state between tests
        from p2m.core import model_client
        model_client._rate_limiter = _ModelRateLimiter()
        self.fake_litellm = _make_litellm_module()

    async def test_success_on_first_attempt(self) -> None:
        call_fn = AsyncMock(return_value="ok")
        with patch("p2m.core.model_client._get_litellm_module", return_value=self.fake_litellm):
            result = await _with_retries(call_fn, model="m")
        self.assertEqual(result, "ok")
        call_fn.assert_called_once()

    async def test_retries_on_rate_limit_then_succeeds(self) -> None:
        rate_exc = self.fake_litellm.RateLimitError("429")
        call_fn = AsyncMock(side_effect=[rate_exc, rate_exc, "ok"])

        with patch("p2m.core.model_client._get_litellm_module", return_value=self.fake_litellm):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await _with_retries(call_fn, model="m")

        self.assertEqual(result, "ok")
        self.assertEqual(call_fn.call_count, 3)

    async def test_retries_on_provider_error_then_succeeds(self) -> None:
        api_exc = self.fake_litellm.APIError("500")
        call_fn = AsyncMock(side_effect=[api_exc, "ok"])

        with patch("p2m.core.model_client._get_litellm_module", return_value=self.fake_litellm):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await _with_retries(call_fn, model="m")

        self.assertEqual(result, "ok")
        self.assertEqual(call_fn.call_count, 2)

    async def test_raises_after_max_retries_exhausted(self) -> None:
        rate_exc = self.fake_litellm.RateLimitError("429")
        call_fn = AsyncMock(side_effect=rate_exc)

        with patch("p2m.core.model_client._get_litellm_module", return_value=self.fake_litellm):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                with self.assertRaises(LLMRateLimitError):
                    await _with_retries(call_fn, model="m")

        # _MAX_RETRIES + 1 = 6 total attempts
        self.assertEqual(call_fn.call_count, 6)

    async def test_non_retryable_error_raises_immediately(self) -> None:
        auth_exc = self.fake_litellm.AuthenticationError("bad key")
        call_fn = AsyncMock(side_effect=auth_exc)

        with patch("p2m.core.model_client._get_litellm_module", return_value=self.fake_litellm):
            with self.assertRaises(Exception) as ctx:
                await _with_retries(call_fn, model="m")

        call_fn.assert_called_once()
        self.assertIn("Authentication", str(ctx.exception))

    async def test_rate_limit_uses_retry_after_header(self) -> None:
        rate_exc = self.fake_litellm.RateLimitError("429")
        rate_exc.headers = {"Retry-After": "5"}
        call_fn = AsyncMock(side_effect=[rate_exc, "ok"])

        from p2m.core import model_client
        observed_base: list[float | None] = []
        original_success = model_client._rate_limiter.report_success

        def spy_success(model: str) -> None:
            # Capture base cooldown before it gets cleared
            observed_base.append(model_client._rate_limiter._base_cooldown.get(model))
            original_success(model)

        model_client._rate_limiter.report_success = spy_success  # type: ignore[assignment]

        with patch("p2m.core.model_client._get_litellm_module", return_value=self.fake_litellm):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await _with_retries(call_fn, model="m")

        self.assertEqual(result, "ok")
        # Rate limiter adopted the server's Retry-After as base before success reset it
        self.assertEqual(observed_base, [5.0])

    async def test_label_does_not_affect_behavior(self) -> None:
        call_fn = AsyncMock(return_value="ok")
        with patch("p2m.core.model_client._get_litellm_module", return_value=self.fake_litellm):
            result = await _with_retries(call_fn, model="m", label="seeds:prompt:slug")
        self.assertEqual(result, "ok")

    async def test_provider_error_uses_exponential_backoff(self) -> None:
        api_exc = self.fake_litellm.APIError("500")
        call_fn = AsyncMock(side_effect=[api_exc, api_exc, "ok"])
        sleep_delays: list[float] = []

        async def track_sleep(s: float) -> None:
            sleep_delays.append(s)

        with patch("p2m.core.model_client._get_litellm_module", return_value=self.fake_litellm):
            with patch("asyncio.sleep", side_effect=track_sleep):
                with patch("random.uniform", return_value=0.0):
                    result = await _with_retries(call_fn, model="m")

        self.assertEqual(result, "ok")
        # Two retries → two sleeps, second should be >= first
        self.assertEqual(len(sleep_delays), 2)
        self.assertGreaterEqual(sleep_delays[1], sleep_delays[0])


# ── Integration: generate() with retries ──────────────────────


class GenerateWithRetriesTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        from p2m.core import model_client
        model_client._rate_limiter = _ModelRateLimiter()
        self.fake_litellm = _make_litellm_module()

    async def test_generate_retries_on_rate_limit(self) -> None:
        from p2m.core import model_client

        rate_exc = self.fake_litellm.RateLimitError("429")
        call_count = 0

        async def fake_acompletion(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise rate_exc
            return {
                "id": "resp-1",
                "model": "openai/gpt-5-mini",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "hello"},
                    }
                ],
            }

        self.fake_litellm.acompletion = fake_acompletion

        with patch.object(model_client, "_get_litellm_module", return_value=self.fake_litellm):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                response = await model_client.generate("openai/gpt-5-mini", "say hi")

        self.assertEqual(response.text, "hello")
        self.assertEqual(call_count, 3)


if __name__ == "__main__":
    unittest.main()
