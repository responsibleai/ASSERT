# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from assert_ai.core import model_client


class ModelClientTest(unittest.IsolatedAsyncioTestCase):
    async def test_generate_uses_acompletion_and_normalizes_response(self) -> None:
        captured: dict[str, object] = {}

        async def fake_acompletion(**kwargs):
            captured.update(kwargs)
            return {
                "id": "resp-chat-1",
                "model": "openai/gpt-5-mini",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": "hello world",
                            "tool_calls": None,
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 7,
                    "total_tokens": 18,
                },
            }

        fake_litellm = SimpleNamespace(acompletion=fake_acompletion)
        options = model_client.GenerateOptions(temperature=0.2, max_tokens=64)

        with patch.object(model_client, "_get_litellm_module", return_value=fake_litellm):
            response = await model_client.generate(
                "openai/gpt-5-mini",
                "say hi",
                options,
            )

        self.assertEqual(
            captured["messages"],
            [{"role": "user", "content": "say hi"}],
        )
        self.assertEqual(captured["temperature"], 0.2)
        self.assertEqual(captured["max_tokens"], 64)
        self.assertEqual(response.text, "hello world")
        self.assertEqual(response.finish_reason, "stop")
        self.assertEqual(response.usage.total_tokens, 18)
        self.assertEqual(response.api_mode, "chat_completion")
        self.assertEqual(response.request_payload["model"], "openai/gpt-5-mini")
        self.assertEqual(response.request_payload["messages"], [{"role": "user", "content": "say hi"}])

    async def test_generate_structured_adds_json_schema_response_format(self) -> None:
        captured: dict[str, object] = {}

        async def fake_acompletion(**kwargs):
            captured.update(kwargs)
            return {
                "id": "resp-structured-1",
                "model": "openai/gpt-5-mini",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": '{"verdict": "pass"}',
                        },
                    }
                ],
            }

        fake_litellm = SimpleNamespace(acompletion=fake_acompletion)
        schema = {
            "type": "object",
            "properties": {
                "verdict": {"type": "string"},
            },
            "required": ["verdict"],
            "additionalProperties": False,
        }

        with patch.object(model_client, "_get_litellm_module", return_value=fake_litellm):
            response = await model_client.generate_structured(
                "openai/gpt-5-mini",
                [{"role": "user", "content": "judge this"}],
                schema_name="judge_output",
                json_schema=schema,
            )

        response_format = captured["response_format"]
        self.assertEqual(response_format["type"], "json_schema")
        self.assertEqual(response_format["json_schema"]["name"], "judge_output")
        self.assertEqual(response_format["json_schema"]["schema"], schema)
        self.assertEqual(response.parsed, {"verdict": "pass"})

    async def test_generate_structured_with_web_search_rejects_gemini(self) -> None:
        schema = {
            "type": "object",
            "properties": {"verdict": {"type": "string"}},
            "required": ["verdict"],
            "additionalProperties": False,
        }

        with patch.object(model_client, "_get_litellm_module") as get_litellm:
            with self.assertRaisesRegex(ValueError, "web_search.*gemini/gemini-2.5-flash"):
                await model_client.generate_structured(
                    "gemini/gemini-2.5-flash",
                    "research this",
                    schema_name="judge_output",
                    json_schema=schema,
                    options=model_client.GenerateOptions(web_search=True),
                )

        get_litellm.assert_not_called()

    async def test_generate_with_web_search_rejects_non_openai_provider(self) -> None:
        with patch.object(model_client, "_get_litellm_module") as get_litellm:
            with self.assertRaisesRegex(ValueError, "Disable web_search"):
                await model_client.generate(
                    "anthropic/claude-sonnet-4-20250514",
                    "research this",
                    options=model_client.GenerateOptions(web_search=True),
                )

        get_litellm.assert_not_called()

    async def test_generate_structured_with_web_search_uses_responses_api(self) -> None:
        captured: dict[str, object] = {}

        async def fake_aresponses(**kwargs):
            captured.update(kwargs)
            return {
                "id": "resp-structured-search-1",
                "model": "openai/gpt-5-mini",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": '{"verdict": "pass"}',
                            }
                        ],
                    }
                ],
            }

        fake_litellm = SimpleNamespace(aresponses=fake_aresponses)
        schema = {
            "type": "object",
            "properties": {
                "verdict": {"type": "string"},
            },
            "required": ["verdict"],
            "additionalProperties": False,
        }

        with patch.object(model_client, "_get_litellm_module", return_value=fake_litellm):
            response = await model_client.generate_structured(
                "openai/gpt-5-mini",
                "research this",
                schema_name="judge_output",
                json_schema=schema,
                options=model_client.GenerateOptions(web_search=True, reasoning_effort="high"),
            )

        self.assertEqual(captured["input"], "research this")
        self.assertEqual(captured["reasoning_effort"], "high")
        self.assertEqual(captured["tools"], [{"type": "web_search_preview"}])
        self.assertEqual(captured["text"]["format"]["type"], "json_schema")
        self.assertEqual(captured["text"]["format"]["name"], "judge_output")
        self.assertEqual(captured["text"]["format"]["schema"], schema)
        self.assertEqual(response.parsed, {"verdict": "pass"})

    async def test_generate_with_tools_normalizes_tool_calls(self) -> None:
        async def fake_acompletion(**_kwargs):
            return {
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "send_message",
                                        "arguments": '{"message": "hello"}',
                                    },
                                }
                            ],
                        },
                    }
                ]
            }

        fake_litellm = SimpleNamespace(acompletion=fake_acompletion)
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "send_message",
                    "description": "Send a message",
                    "parameters": {"type": "object"},
                },
            }
        ]

        with patch.object(model_client, "_get_litellm_module", return_value=fake_litellm):
            response = await model_client.generate_with_tools(
                "openai/gpt-5-mini",
                "use a tool",
                tools=tools,
                options=model_client.GenerateOptions(tool_choice="auto"),
            )

        self.assertEqual(len(response.tool_calls), 1)
        self.assertEqual(response.tool_calls[0].call_id, "call_1")
        self.assertEqual(response.tool_calls[0].name, "send_message")
        self.assertEqual(response.tool_calls[0].arguments, {"message": "hello"})
        self.assertEqual(response.api_mode, "chat_completion")
        self.assertEqual(response.request_payload["tools"], tools)
        self.assertEqual(response.request_payload["tool_choice"], "auto")

    async def test_generate_with_web_search_falls_back_to_sync_responses(self) -> None:
        captured: dict[str, object] = {}

        def fake_responses(**kwargs):
            captured.update(kwargs)
            return {
                "id": "resp-search-1",
                "model": "openai/gpt-5-mini",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": "research result",
                            "reasoning_content": "internal reasoning",
                        },
                    }
                ],
            }

        fake_litellm = SimpleNamespace(responses=fake_responses)
        options = model_client.GenerateOptions(
            web_search=True,
            reasoning_effort="high",
            max_output_tokens=2048,
        )

        with patch.object(model_client, "_get_litellm_module", return_value=fake_litellm):
            response = await model_client.generate(
                "openai/gpt-5-mini",
                "research this",
                options,
            )

        self.assertEqual(captured["input"], "research this")
        self.assertEqual(captured["max_output_tokens"], 2048)
        self.assertEqual(captured["reasoning_effort"], "high")
        self.assertEqual(captured["tools"], [{"type": "web_search_preview"}])
        self.assertEqual(response.text, "research result")
        self.assertEqual(response.reasoning, "internal reasoning")


class NormalizeUsageTest(unittest.TestCase):
    """Verify cache-hit token accounting across the OpenAI/Azure and Anthropic shapes."""

    def test_extracts_openai_chat_cached_tokens(self) -> None:
        # Chat Completions (OpenAI/Azure) puts cache hits under
        # prompt_tokens_details.cached_tokens.
        usage = model_client._normalize_usage({
            "prompt_tokens": 5000,
            "completion_tokens": 200,
            "total_tokens": 5200,
            "prompt_tokens_details": {"cached_tokens": 4096},
        })
        assert usage is not None
        self.assertEqual(usage.prompt_tokens, 5000)
        self.assertEqual(usage.completion_tokens, 200)
        self.assertEqual(usage.total_tokens, 5200)
        self.assertEqual(usage.cached_input_tokens, 4096)
        self.assertIsNone(usage.cache_creation_input_tokens)

    def test_extracts_openai_responses_cached_tokens(self) -> None:
        # Responses API uses input_tokens_details.cached_tokens instead.
        usage = model_client._normalize_usage({
            "input_tokens": 3000,
            "output_tokens": 150,
            "total_tokens": 3150,
            "input_tokens_details": {"cached_tokens": 2048},
        })
        assert usage is not None
        self.assertEqual(usage.cached_input_tokens, 2048)

    def test_extracts_anthropic_cache_tokens(self) -> None:
        # Anthropic surfaces both read and creation counts at the top level.
        usage = model_client._normalize_usage({
            "prompt_tokens": 5000,
            "completion_tokens": 200,
            "total_tokens": 5200,
            "cache_read_input_tokens": 4096,
            "cache_creation_input_tokens": 800,
        })
        assert usage is not None
        self.assertEqual(usage.cached_input_tokens, 4096)
        self.assertEqual(usage.cache_creation_input_tokens, 800)

    def test_anthropic_top_level_takes_precedence_over_details(self) -> None:
        # If both shapes are present (rare but possible via litellm
        # provider-translation glue) the explicit top-level field wins.
        usage = model_client._normalize_usage({
            "prompt_tokens": 100,
            "completion_tokens": 10,
            "total_tokens": 110,
            "cache_read_input_tokens": 64,
            "prompt_tokens_details": {"cached_tokens": 32},
        })
        assert usage is not None
        self.assertEqual(usage.cached_input_tokens, 64)

    def test_no_cache_metadata_leaves_fields_none(self) -> None:
        usage = model_client._normalize_usage({
            "prompt_tokens": 100,
            "completion_tokens": 10,
            "total_tokens": 110,
        })
        assert usage is not None
        self.assertIsNone(usage.cached_input_tokens)
        self.assertIsNone(usage.cache_creation_input_tokens)

    def test_summarize_response_surfaces_cache_when_present(self) -> None:
        response = model_client.ModelResponse(
            text="ok",
            finish_reason="stop",
            usage=model_client.UsageStats(
                prompt_tokens=5000,
                completion_tokens=200,
                total_tokens=5200,
                cached_input_tokens=4096,
            ),
        )
        summary = model_client.summarize_response(response)
        self.assertEqual(summary["usage"]["cached_input_tokens"], 4096)

    def test_summarize_response_omits_cache_when_zero(self) -> None:
        response = model_client.ModelResponse(
            text="ok",
            finish_reason="stop",
            usage=model_client.UsageStats(
                prompt_tokens=5000,
                completion_tokens=200,
                total_tokens=5200,
                cached_input_tokens=0,
            ),
        )
        summary = model_client.summarize_response(response)
        self.assertNotIn("cached_input_tokens", summary["usage"])


class UsageAccumulatorTest(unittest.TestCase):
    """Track token usage across many ``generate*`` calls inside one scope."""

    def test_add_aggregates_totals_and_per_model(self) -> None:
        acc = model_client.UsageAccumulator()
        acc.add(
            model_client.UsageStats(
                prompt_tokens=100,
                completion_tokens=50,
                total_tokens=150,
                cached_input_tokens=20,
            ),
            model="azure/gpt-5.4-mini",
        )
        acc.add(
            model_client.UsageStats(
                prompt_tokens=200,
                completion_tokens=80,
                total_tokens=280,
                cached_input_tokens=80,
            ),
            model="azure/gpt-5.4-mini",
        )
        self.assertEqual(acc.calls, 2)
        self.assertEqual(acc.input_tokens, 300)
        self.assertEqual(acc.output_tokens, 130)
        self.assertEqual(acc.cached_input_tokens, 100)
        self.assertAlmostEqual(acc.cache_hit_rate(), 100 / 300)
        per_model = acc.per_model["azure/gpt-5.4-mini"]
        self.assertEqual(per_model["calls"], 2)
        self.assertEqual(per_model["input_tokens"], 300)
        self.assertEqual(per_model["cached_input_tokens"], 100)

    def test_add_handles_none_usage_silently(self) -> None:
        acc = model_client.UsageAccumulator()
        acc.add(None, model="azure/gpt-5.4-mini")
        self.assertEqual(acc.calls, 0)
        self.assertEqual(acc.input_tokens, 0)

    def test_cache_hit_rate_is_zero_when_no_input_tokens(self) -> None:
        acc = model_client.UsageAccumulator()
        self.assertEqual(acc.cache_hit_rate(), 0.0)

    def test_to_dict_is_json_serializable(self) -> None:
        import json

        acc = model_client.UsageAccumulator()
        acc.add(
            model_client.UsageStats(
                prompt_tokens=10, completion_tokens=5, total_tokens=15
            ),
            model="azure/gpt-5.4-mini",
        )
        encoded = json.dumps(acc.to_dict())
        decoded = json.loads(encoded)
        self.assertEqual(decoded["calls"], 1)
        self.assertEqual(decoded["per_model"]["azure/gpt-5.4-mini"]["calls"], 1)


class TrackUsageTest(unittest.IsolatedAsyncioTestCase):
    """``track_usage`` collects every ``generate*`` call inside the block."""

    async def test_track_usage_collects_concurrent_generate_calls(self) -> None:
        async def fake_acompletion(**kwargs):
            return {
                "id": "r",
                "model": kwargs["model"],
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "ok"},
                    }
                ],
                "usage": {
                    "prompt_tokens": 1024,
                    "completion_tokens": 32,
                    "total_tokens": 1056,
                    "prompt_tokens_details": {"cached_tokens": 512},
                },
            }

        fake_litellm = SimpleNamespace(acompletion=fake_acompletion)
        with patch.object(model_client, "_get_litellm_module", return_value=fake_litellm):
            with model_client.track_usage() as usage:
                import asyncio

                await asyncio.gather(
                    model_client.generate("azure/gpt-5.4-mini", "hi"),
                    model_client.generate("azure/gpt-5.4-mini", "hi"),
                    model_client.generate("azure/gpt-5.4-mini", "hi"),
                )
        self.assertEqual(usage.calls, 3)
        self.assertEqual(usage.input_tokens, 3 * 1024)
        self.assertEqual(usage.output_tokens, 3 * 32)
        self.assertEqual(usage.cached_input_tokens, 3 * 512)
        self.assertIn("azure/gpt-5.4-mini", usage.per_model)

    async def test_record_usage_outside_scope_is_a_noop(self) -> None:
        # Should not raise even when no accumulator is active.
        model_client._record_usage(
            model_client.UsageStats(prompt_tokens=10, completion_tokens=5),
            model="azure/gpt-5.4-mini",
        )

    async def test_nested_scopes_isolate_accumulators(self) -> None:
        usage_outer = model_client.UsageStats(prompt_tokens=100, completion_tokens=10)
        usage_inner = model_client.UsageStats(prompt_tokens=200, completion_tokens=20)
        with model_client.track_usage() as outer:
            model_client._record_usage(usage_outer, model="m")
            with model_client.track_usage() as inner:
                model_client._record_usage(usage_inner, model="m")
            model_client._record_usage(usage_outer, model="m")
        self.assertEqual(inner.calls, 1)
        self.assertEqual(inner.input_tokens, 200)
        self.assertEqual(outer.calls, 2)
        self.assertEqual(outer.input_tokens, 200)


class ResponsesApiClassificationTest(unittest.TestCase):
    """``_classify_llm_error`` must route Azure's region-rejection messages
    to ``_ResponsesApiNotAvailableError`` *before* the generic BadRequest /
    NotFound handlers run, so ``_with_retries`` can demote to chat
    completions instead of returning an unrecoverable ``LLMInputError``.
    """

    def setUp(self) -> None:
        # Build a litellm stand-in with just the exception classes
        # ``_classify_llm_error`` consults via isinstance().
        self.fake_litellm = SimpleNamespace()
        self.fake_litellm.AuthenticationError = type("AuthenticationError", (Exception,), {})
        self.fake_litellm.RateLimitError = type("RateLimitError", (Exception,), {})
        self.fake_litellm.BadRequestError = type("BadRequestError", (Exception,), {})
        self.fake_litellm.NotFoundError = type("NotFoundError", (Exception,), {})
        self.fake_litellm.APIError = type("APIError", (Exception,), {})
        self.fake_litellm.APIConnectionError = type("APIConnectionError", (Exception,), {})
        # Preserve and reset the sticky demotion flag between tests.
        self._saved_force = model_client._force_chat_completions
        model_client._force_chat_completions = False

    def tearDown(self) -> None:
        model_client._force_chat_completions = self._saved_force

    def _classify(self, exc: Exception) -> Exception:
        with patch.object(model_client, "_get_litellm_module", return_value=self.fake_litellm):
            return model_client._classify_llm_error(exc)

    def test_api_version_not_supported_classifies_as_responses_api_unavailable(self) -> None:
        # Observed in West Europe (keweu) — Azure rejects the Responses API
        # request as HTTP 400 / BadRequestError with this message. Must be
        # detected before the BadRequestError branch returns LLMInputError.
        exc = self.fake_litellm.BadRequestError(
            'AzureException - {"error":{"code":"BadRequest","message":"API version not supported"}}'
        )
        classified = self._classify(exc)
        self.assertIsInstance(classified, model_client._ResponsesApiNotAvailableError)
        self.assertIs(classified.__cause__, exc)

    def test_responses_api_not_enabled_marker_classifies_as_responses_api_unavailable(self) -> None:
        # Older Azure deployments surface a 404 with this message. Keep
        # both markers supported so the fallback works across regions.
        exc = self.fake_litellm.NotFoundError("Responses API is not enabled for this deployment")
        classified = self._classify(exc)
        self.assertIsInstance(classified, model_client._ResponsesApiNotAvailableError)
        self.assertIs(classified.__cause__, exc)

    def test_api_version_not_supported_with_force_chat_still_classifies_as_responses_api_unavailable(self) -> None:
        # ``_classify_llm_error`` must NOT gate on ``_force_chat_completions``.
        # At high concurrency the first task activates fallback while other
        # tasks are still in-flight against the Responses API; those
        # in-flight calls surface the same marker right after fallback is
        # active. Gating the classifier on the flag caused those failures
        # to drop into the BadRequestError → LLMInputError path and never
        # retry on the Chat path, breaking the whole run. Per-task loop
        # prevention is the responsibility of ``_with_retries``.
        model_client._force_chat_completions = True
        exc = self.fake_litellm.BadRequestError(
            'AzureException - {"error":{"code":"BadRequest","message":"API version not supported"}}'
        )
        classified = self._classify(exc)
        self.assertIsInstance(classified, model_client._ResponsesApiNotAvailableError)
        self.assertIs(classified.__cause__, exc)

    def test_responses_api_marker_with_force_chat_still_classifies_as_responses_api_unavailable(self) -> None:
        # Same race-condition guard as above but for the 404 marker.
        model_client._force_chat_completions = True
        exc = self.fake_litellm.NotFoundError("Responses API is not enabled for this deployment")
        classified = self._classify(exc)
        self.assertIsInstance(classified, model_client._ResponsesApiNotAvailableError)
        self.assertIs(classified.__cause__, exc)

    def test_regular_bad_request_still_classifies_as_input_error(self) -> None:
        # Non-marker BadRequestError must continue to map to LLMInputError
        # (no regression on the common content-filter / prompt-too-long path).
        exc = self.fake_litellm.BadRequestError("Invalid prompt: missing required field")
        classified = self._classify(exc)
        self.assertIsInstance(classified, model_client.LLMInputError)
        self.assertNotIsInstance(classified, model_client._ResponsesApiNotAvailableError)


class WithRetriesResponsesApiFallbackTest(unittest.IsolatedAsyncioTestCase):
    """``_with_retries`` must give every task its own Chat-path retry
    budget. The global ``_force_chat_completions`` flag can already be
    True when a task hits the marker (because a concurrent task tripped
    it first) — the task must still be allowed one retry on the Chat
    path, otherwise concurrent in-flight Responses-API calls all fail
    after the first WARN.
    """

    def setUp(self) -> None:
        self.fake_litellm = SimpleNamespace()
        self.fake_litellm.AuthenticationError = type("AuthenticationError", (Exception,), {})
        self.fake_litellm.RateLimitError = type("RateLimitError", (Exception,), {})
        self.fake_litellm.BadRequestError = type("BadRequestError", (Exception,), {})
        self.fake_litellm.NotFoundError = type("NotFoundError", (Exception,), {})
        self.fake_litellm.APIError = type("APIError", (Exception,), {})
        self.fake_litellm.APIConnectionError = type("APIConnectionError", (Exception,), {})
        self._saved_force = model_client._force_chat_completions
        self._saved_warned = model_client._responses_api_fallback_warned
        model_client._force_chat_completions = False
        model_client._responses_api_fallback_warned = False

    def tearDown(self) -> None:
        model_client._force_chat_completions = self._saved_force
        model_client._responses_api_fallback_warned = self._saved_warned

    async def test_retry_succeeds_when_fallback_already_active(self) -> None:
        # Simulates a concurrent in-flight Responses-API call that lands
        # after another task already activated fallback. Pre-fix this
        # call returned LLMInputError and was never retried.
        model_client._force_chat_completions = True
        calls = {"n": 0}

        async def call_fn():
            calls["n"] += 1
            if calls["n"] == 1:
                raise self.fake_litellm.NotFoundError(
                    "Responses API is not enabled for this deployment"
                )
            return "ok"

        with patch.object(model_client, "_get_litellm_module", return_value=self.fake_litellm):
            result = await model_client._with_retries(call_fn, model="azure/gpt-5.4")
        self.assertEqual(result, "ok")
        self.assertEqual(calls["n"], 2)

    async def test_re_raises_when_chat_path_retry_also_fails(self) -> None:
        # If the Chat-path retry itself surfaces the same marker, the
        # Chat path is genuinely broken (e.g. wrong api-version) —
        # re-raise instead of looping forever.
        calls = {"n": 0}

        async def call_fn():
            calls["n"] += 1
            raise self.fake_litellm.BadRequestError(
                'AzureException - {"error":{"message":"API version not supported"}}'
            )

        with patch.object(model_client, "_get_litellm_module", return_value=self.fake_litellm):
            with self.assertRaises(model_client._ResponsesApiNotAvailableError):
                await model_client._with_retries(call_fn, model="azure/gpt-5.4")
        # Two calls total: original + one Chat-path retry, then re-raise.
        self.assertEqual(calls["n"], 2)


class WebSearchFallbackDegradationTest(unittest.IsolatedAsyncioTestCase):
    """When the Responses API is unavailable in a region, ``web_search``
    calls cannot be retried as-is on Chat Completions (there is no
    Chat-Completions equivalent for ``web_search_preview``). The
    fallback layer must drop ``web_search`` and route via Chat
    Completions instead — either proactively (when the global
    fallback is already active) or reactively (when this task is the
    first to hit the region marker).
    """

    def setUp(self) -> None:
        self._saved_force = model_client._force_chat_completions
        self._saved_warned = model_client._responses_api_fallback_warned
        self._saved_drop_warned = model_client._web_search_drop_warned
        # Capture the bridge-check state so the reactive test, which
        # triggers a real ``_install_responses_api_guard()`` call,
        # cannot leak its monkey-patch into subsequent tests.
        self._saved_guard_installed = model_client._responses_api_guard_installed
        try:
            from litellm import main as _litellm_main  # noqa: WPS433
            self._litellm_main = _litellm_main
            self._saved_bridge_check = _litellm_main.responses_api_bridge_check
        except ImportError:
            self._litellm_main = None
            self._saved_bridge_check = None
        model_client._force_chat_completions = False
        model_client._responses_api_fallback_warned = False
        model_client._web_search_drop_warned = False

    def tearDown(self) -> None:
        model_client._force_chat_completions = self._saved_force
        model_client._responses_api_fallback_warned = self._saved_warned
        model_client._web_search_drop_warned = self._saved_drop_warned
        # Restore the bridge-check monkey-patch state so other tests
        # see a clean LiteLLM module.
        if self._litellm_main is not None and self._saved_bridge_check is not None:
            self._litellm_main.responses_api_bridge_check = self._saved_bridge_check
        model_client._responses_api_guard_installed = self._saved_guard_installed

    async def test_web_search_dropped_proactively_when_fallback_active(self) -> None:
        # Simulates a task entering ``generate_structured`` after another
        # task has already activated the Chat-Completions fallback. The
        # web_search request must be downgraded up front (no Responses
        # API round-trip) and emit a one-time WARN.
        captured: dict[str, object] = {}

        async def fake_acompletion(**kwargs):
            captured.update(kwargs)
            return {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": '{"verdict": "pass"}',
                        },
                    }
                ]
            }

        fake_litellm = SimpleNamespace(acompletion=fake_acompletion)
        schema = {
            "type": "object",
            "properties": {"verdict": {"type": "string"}},
            "required": ["verdict"],
            "additionalProperties": False,
        }
        model_client._force_chat_completions = True

        with patch.object(model_client, "_get_litellm_module", return_value=fake_litellm), \
                self.assertLogs(model_client.log, level="WARNING") as cm:
            response = await model_client.generate_structured(
                "azure/gpt-5.4",
                "research this",
                schema_name="judge_output",
                json_schema=schema,
                options=model_client.GenerateOptions(web_search=True),
            )

        # Routed through Chat Completions (no Responses API tool).
        self.assertNotIn("tools", captured)
        self.assertIn("response_format", captured)
        self.assertEqual(response.api_mode, "chat_completion")
        self.assertEqual(response.parsed, {"verdict": "pass"})
        # Exactly one WARN naming the model.
        warn_lines = [r for r in cm.output if "dropping web_search" in r]
        self.assertEqual(len(warn_lines), 1)
        self.assertIn("azure/gpt-5.4", warn_lines[0])

    async def test_web_search_dropped_reactively_on_responses_api_unavailable(self) -> None:
        # The first call hits the Responses API and gets the region
        # marker. ``_with_retries`` retries once on the same closure
        # (still web_search), then re-raises ``_ResponsesApiNotAvailableError``.
        # ``generate_structured`` must catch and recurse without
        # web_search, succeeding via Chat Completions.
        responses_calls = {"n": 0}
        completion_calls: dict[str, object] = {}

        class NotFoundError(Exception):
            pass

        async def fake_aresponses(**_kwargs):
            responses_calls["n"] += 1
            raise NotFoundError(
                "Responses API is not enabled for this deployment"
            )

        async def fake_acompletion(**kwargs):
            completion_calls.update(kwargs)
            return {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": '{"verdict": "pass"}',
                        },
                    }
                ]
            }

        fake_litellm = SimpleNamespace(
            aresponses=fake_aresponses,
            acompletion=fake_acompletion,
            AuthenticationError=type("AuthenticationError", (Exception,), {}),
            RateLimitError=type("RateLimitError", (Exception,), {}),
            BadRequestError=type("BadRequestError", (Exception,), {}),
            NotFoundError=NotFoundError,
            APIError=type("APIError", (Exception,), {}),
            APIConnectionError=type("APIConnectionError", (Exception,), {}),
        )
        schema = {
            "type": "object",
            "properties": {"verdict": {"type": "string"}},
            "required": ["verdict"],
            "additionalProperties": False,
        }

        with patch.object(model_client, "_get_litellm_module", return_value=fake_litellm), \
                self.assertLogs(model_client.log, level="WARNING") as cm:
            response = await model_client.generate_structured(
                "azure/gpt-5.4",
                "research this",
                schema_name="judge_output",
                json_schema=schema,
                options=model_client.GenerateOptions(web_search=True),
            )

        # ``_with_retries`` attempts the Responses call twice before
        # giving up (original + one Chat-path retry that's still the
        # responses closure), then ``generate_structured`` recurses
        # without web_search.
        self.assertEqual(responses_calls["n"], 2)
        self.assertNotIn("tools", completion_calls)
        self.assertIn("response_format", completion_calls)
        self.assertEqual(response.api_mode, "chat_completion")
        self.assertEqual(response.parsed, {"verdict": "pass"})
        # Global fallback got activated as a side-effect of the retry.
        self.assertTrue(model_client._force_chat_completions)
        # Both the activation WARN and the web_search drop WARN appear.
        drop_lines = [r for r in cm.output if "dropping web_search" in r]
        self.assertEqual(len(drop_lines), 1)

    async def test_web_search_drop_warning_logged_once_per_run(self) -> None:
        # Multiple proactive degradations in the same run must produce
        # only ONE WARN — the global ``_web_search_drop_warned`` flag.
        async def fake_acompletion(**_kwargs):
            return {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "ok"},
                    }
                ]
            }

        fake_litellm = SimpleNamespace(acompletion=fake_acompletion)
        model_client._force_chat_completions = True
        opts = model_client.GenerateOptions(web_search=True)

        with patch.object(model_client, "_get_litellm_module", return_value=fake_litellm), \
                self.assertLogs(model_client.log, level="WARNING") as cm:
            await model_client.generate("azure/gpt-5.4", "first", opts)
            await model_client.generate("azure/gpt-5.4", "second", opts)

        drop_lines = [r for r in cm.output if "dropping web_search" in r]
        self.assertEqual(len(drop_lines), 1)

    async def test_proactive_degradation_skipped_when_web_search_off(self) -> None:
        # When web_search is already off, the proactive degradation
        # branch must be a no-op — no WARN, normal Chat-path call.
        async def fake_acompletion(**_kwargs):
            return {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "ok"},
                    }
                ]
            }

        fake_litellm = SimpleNamespace(acompletion=fake_acompletion)
        model_client._force_chat_completions = True

        with patch.object(model_client, "_get_litellm_module", return_value=fake_litellm):
            response = await model_client.generate(
                "azure/gpt-5.4",
                "hello",
                model_client.GenerateOptions(web_search=False),
            )
        self.assertEqual(response.text, "ok")
        self.assertFalse(model_client._web_search_drop_warned)


class NormalizeAzureApiBaseTest(unittest.TestCase):
    """``_normalize_azure_api_base`` must strip any ``/openai/...``
    path suffix that snuck into AZURE_API_BASE. LiteLLM appends that
    path itself, so leaving it in causes malformed URLs.
    """

    def setUp(self) -> None:
        self._saved_base = os.environ.get("AZURE_API_BASE")

    def tearDown(self) -> None:
        if self._saved_base is None:
            os.environ.pop("AZURE_API_BASE", None)
        else:
            os.environ["AZURE_API_BASE"] = self._saved_base

    def _normalize(self, value: str) -> str:
        os.environ["AZURE_API_BASE"] = value
        model_client._normalize_azure_api_base()
        return os.environ["AZURE_API_BASE"]

    def test_bare_endpoint_unchanged_when_already_trailing_slash(self) -> None:
        self.assertEqual(
            self._normalize("https://example.openai.azure.com/"),
            "https://example.openai.azure.com/",
        )

    def test_bare_endpoint_gets_trailing_slash(self) -> None:
        self.assertEqual(
            self._normalize("https://example.openai.azure.com"),
            "https://example.openai.azure.com/",
        )

    def test_strips_openai_responses_path(self) -> None:
        self.assertEqual(
            self._normalize(
                "https://example.services.ai.azure.com/openai/v1/responses"
            ),
            "https://example.services.ai.azure.com/",
        )

    def test_strips_openai_deployments_path(self) -> None:
        self.assertEqual(
            self._normalize(
                "https://example.openai.azure.com/openai/deployments/gpt-5.4"
            ),
            "https://example.openai.azure.com/",
        )

    def test_preserves_project_prefix_before_openai(self) -> None:
        # Account-level projects path before /openai must be preserved.
        self.assertEqual(
            self._normalize(
                "https://example.services.ai.azure.com/api/projects/myproj"
                "/openai/v1/responses"
            ),
            "https://example.services.ai.azure.com/api/projects/myproj/",
        )

    def test_empty_value_is_noop(self) -> None:
        os.environ.pop("AZURE_API_BASE", None)
        model_client._normalize_azure_api_base()
        self.assertNotIn("AZURE_API_BASE", os.environ)


class ResponsesApiGuardForwardingTest(unittest.TestCase):
    """``_install_responses_api_guard`` must forward all positional and
    keyword arguments to LiteLLM's original ``responses_api_bridge_check``
    so the patch survives minor LiteLLM upgrades that add new kwargs.
    """

    def setUp(self) -> None:
        from litellm import main as _litellm_main  # noqa: WPS433
        self._litellm_main = _litellm_main
        self._saved_bridge_check = _litellm_main.responses_api_bridge_check
        self._saved_guard_installed = model_client._responses_api_guard_installed
        self._saved_force = model_client._force_chat_completions

    def tearDown(self) -> None:
        self._litellm_main.responses_api_bridge_check = self._saved_bridge_check
        model_client._responses_api_guard_installed = self._saved_guard_installed
        model_client._force_chat_completions = self._saved_force

    def test_unknown_future_kwargs_reach_original(self) -> None:
        captured: dict[str, object] = {}

        def fake_original(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return ({"mode": "chat"}, args[0] if args else "")

        self._litellm_main.responses_api_bridge_check = fake_original
        # Force a re-install so the patch wraps our fake.
        model_client._responses_api_guard_installed = False
        model_client._install_responses_api_guard()

        # Call with a mix of positional + known + an invented future
        # kwarg. All must reach ``fake_original`` untouched.
        self._litellm_main.responses_api_bridge_check(
            "azure/gpt-5.4",
            "azure",
            web_search_options=None,
            tools=None,
            reasoning_effort="medium",
            future_kwarg_from_litellm_2_0="passthrough",
        )
        self.assertEqual(
            captured["args"],
            ("azure/gpt-5.4", "azure"),
        )
        self.assertEqual(
            captured["kwargs"],
            {
                "web_search_options": None,
                "tools": None,
                "reasoning_effort": "medium",
                "future_kwarg_from_litellm_2_0": "passthrough",
            },
        )


class IsTruncatedResponseTest(unittest.TestCase):
    """Cross-API truncation detection — see issue #131."""

    def test_chat_completions_length_finish_reason(self) -> None:
        """OpenAI/Azure Chat Completions reports 'length' on output-cap truncation."""
        response = model_client.ModelResponse(text="partial", finish_reason="length")
        self.assertTrue(model_client.is_truncated_response(response))

    def test_responses_api_max_output_tokens_finish_reason(self) -> None:
        """Responses API surfaces 'max_output_tokens' via normalize_response."""
        response = model_client.ModelResponse(
            text="partial",
            finish_reason="max_output_tokens",
        )
        self.assertTrue(model_client.is_truncated_response(response))

    def test_responses_api_max_tokens_finish_reason(self) -> None:
        """LiteLLM / older Responses API variants emit 'max_tokens'."""
        response = model_client.ModelResponse(text="partial", finish_reason="max_tokens")
        self.assertTrue(model_client.is_truncated_response(response))

    def test_falls_back_to_incomplete_details_reason(self) -> None:
        """Belt-and-suspenders: raw incomplete_details.reason still flags truncation."""
        response = model_client.ModelResponse(
            text="partial",
            finish_reason=None,
            incomplete_details={"reason": "max_output_tokens"},
        )
        self.assertTrue(model_client.is_truncated_response(response))

    def test_bare_incomplete_status_is_not_truncation(self) -> None:
        """'incomplete' alone is ambiguous (content filter, provider error, etc.)
        and should NOT trigger a max_tokens retry. Only a specific reason does."""
        response = model_client.ModelResponse(
            text="partial",
            finish_reason="incomplete",
        )
        self.assertFalse(model_client.is_truncated_response(response))

    def test_normal_stop_is_not_truncation(self) -> None:
        response = model_client.ModelResponse(text="full", finish_reason="stop")
        self.assertFalse(model_client.is_truncated_response(response))

    def test_none_finish_reason_is_not_truncation(self) -> None:
        response = model_client.ModelResponse(text="full", finish_reason=None)
        self.assertFalse(model_client.is_truncated_response(response))


class NormalizeUsageZeroTokenDiagnosticsTest(unittest.TestCase):
    """Zero-token usage warrants a debug log so issue #131-style mystery
    '1 call · 0 in / 0 out' rows can be traced back to provider responses."""

    def test_zero_token_usage_emits_debug_log(self) -> None:
        with self.assertLogs("assert_ai.core.model_client", level="DEBUG") as cm:
            stats = model_client._normalize_usage({"prompt_tokens": 0, "completion_tokens": 0})
        self.assertIsNotNone(stats)
        self.assertTrue(any("zero/None tokens" in msg for msg in cm.output))

    def test_real_usage_does_not_emit_zero_token_log(self) -> None:
        with self.assertLogs("assert_ai.core.model_client", level="DEBUG") as cm:
            # Need at least one log so assertLogs doesn't fail; emit a manual one.
            model_client.log.debug("sentinel")
            model_client._normalize_usage({"prompt_tokens": 100, "completion_tokens": 50})
        self.assertFalse(any("zero/None tokens" in msg for msg in cm.output))


# ── Azure AD token provider injection ─────────────────────────


class AzureAadTokenInjectionTest(unittest.IsolatedAsyncioTestCase):
    """The 6-row matrix for ``_maybe_inject_azure_aad_token``.

    Covers every (model_family × auth_mode × dep_available) combination
    that affects whether ``azure_ad_token_provider`` ends up in the
    LiteLLM payload — plus the eager error when AAD is the only viable
    path but azure-identity is missing.
    """

    def _stub_litellm(self, captured: dict[str, object]) -> SimpleNamespace:
        async def fake_acompletion(**kwargs: object) -> dict[str, object]:
            captured.update(kwargs)
            return {
                "id": "resp-aad-1",
                "model": str(kwargs.get("model", "")),
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "ok"},
                    }
                ],
            }

        return SimpleNamespace(acompletion=fake_acompletion)

    async def _run_chat(
        self,
        *,
        model: str,
        mode: object,
        provider: object,
    ) -> dict[str, object]:
        captured: dict[str, object] = {}
        fake_litellm = self._stub_litellm(captured)
        # For azure_ai/* the injection layer re-resolves the auth mode
        # against the family-specific env var (AZURE_AI_API_KEY); for
        # azure/* it reads the cached _AZURE_AUTH_MODE. We patch both
        # sides so a single ``mode`` arg drives the test regardless of
        # family, and we wipe the relevant env vars so a stray
        # AZURE_API_KEY in the dev shell does not leak into the per-family
        # resolver.
        env_overrides = {
            "AZURE_API_KEY": "",
            "AZURE_AI_API_KEY": "",
            "ASSERT_AZURE_USE_AAD": "",
        }
        if mode == "key":
            family = model.split("/", 1)[0]
            if family == "azure_ai":
                env_overrides["AZURE_AI_API_KEY"] = "fake-static-key"
            else:
                env_overrides["AZURE_API_KEY"] = "fake-static-key"
        elif mode == "aad":
            env_overrides["ASSERT_AZURE_USE_AAD"] = "1"
        # mode == "aad-fallback" leaves all three blank.
        with patch.object(model_client, "_get_litellm_module", return_value=fake_litellm), \
             patch.object(model_client.azure_auth, "_AZURE_AUTH_MODE", mode), \
             patch.object(model_client.azure_auth, "get_azure_token_provider", return_value=provider), \
             patch.dict("os.environ", env_overrides, clear=False):
            await model_client.generate(model, "hi")
        return captured

    # Row 1: key + azure/* → no provider injected
    async def test_key_mode_azure_model_no_provider(self) -> None:
        captured = await self._run_chat(
            model="azure/gpt-4o-mini", mode="key", provider=lambda: "tok",
        )
        self.assertNotIn("azure_ad_token_provider", captured)

    # Row 2: aad + azure/* → provider injected
    async def test_aad_mode_azure_model_injects_provider(self) -> None:
        stub = lambda: "stub-token"
        captured = await self._run_chat(
            model="azure/gpt-4o-mini", mode="aad", provider=stub,
        )
        self.assertIs(captured["azure_ad_token_provider"], stub)

    # Row 2b: aad + azure/* + AZURE_API_KEY also present in env → provider
    # still wins, and the injection layer must not leak ``api_key`` into the
    # LiteLLM payload. Pins behavior matching the public docs on
    # ``ASSERT_AZURE_USE_AAD`` precedence (explicit AAD flag wins over key)
    # and protects against a regression where the injection layer would
    # silently fall back to the env key when AAD was explicitly requested.
    async def test_aad_mode_with_azure_api_key_in_env_still_injects_provider(self) -> None:
        stub = lambda: "stub-token"
        with patch.dict(
            "os.environ",
            {"ASSERT_AZURE_USE_AAD": "1", "AZURE_API_KEY": "sk-test-should-be-ignored"},
            clear=False,
        ):
            captured = await self._run_chat(
                model="azure/gpt-4o-mini", mode="aad", provider=stub,
            )
        self.assertIs(captured["azure_ad_token_provider"], stub)
        # The injection layer must not surface ``api_key`` itself — LiteLLM
        # is free to do whatever it wants with the env var, but
        # ``azure_ad_token_provider`` takes precedence inside LiteLLM, so
        # the explicit AAD opt-in continues to win even when the env carries
        # a stale ``AZURE_API_KEY``.
        self.assertNotIn("api_key", captured)

    # Row 3: aad-fallback + azure/* → provider injected
    async def test_aad_fallback_mode_azure_model_injects_provider(self) -> None:
        stub = lambda: "stub-token"
        captured = await self._run_chat(
            model="azure/gpt-4o-mini", mode="aad-fallback", provider=stub,
        )
        self.assertIs(captured["azure_ad_token_provider"], stub)

    # Row 4: aad + non-azure → no provider injected
    async def test_aad_mode_openai_model_no_provider(self) -> None:
        captured = await self._run_chat(
            model="openai/gpt-5-mini", mode="aad", provider=lambda: "tok",
        )
        self.assertNotIn("azure_ad_token_provider", captured)

    # Row 5: key + non-azure → no provider injected
    async def test_key_mode_openai_model_no_provider(self) -> None:
        captured = await self._run_chat(
            model="openai/gpt-5-mini", mode="key", provider=None,
        )
        self.assertNotIn("azure_ad_token_provider", captured)

    # Row 6: aad + azure/* + missing dep → raises LLMAuthError eagerly
    async def test_aad_mode_azure_model_missing_dep_raises(self) -> None:
        with patch.object(model_client.azure_auth, "_AZURE_AUTH_MODE", "aad"), \
             patch.object(model_client.azure_auth, "get_azure_token_provider", return_value=None):
            with self.assertRaises(model_client.LLMAuthError) as ctx:
                await model_client.generate("azure/gpt-4o-mini", "hi")
        self.assertIn("azure-identity", str(ctx.exception))
        self.assertIn("assert-ai[azure-aad]", str(ctx.exception))

    # Row 6b: aad-fallback + azure/* + missing dep → silent skip (no inject, no raise)
    async def test_aad_fallback_missing_dep_silently_skips_injection(self) -> None:
        captured = await self._run_chat(
            model="azure/gpt-4o-mini", mode="aad-fallback", provider=None,
        )
        self.assertNotIn("azure_ad_token_provider", captured)

    async def test_user_extra_kwargs_override_injected_provider(self) -> None:
        """Explicit user-supplied provider must always win over the auto one."""
        user_provider = lambda: "user-token"
        captured = await self._run_chat(
            model="azure/gpt-4o-mini",
            mode="aad",
            provider=lambda: "auto-token",
        )
        # Sanity: auto provider was injected
        self.assertIsNotNone(captured.get("azure_ad_token_provider"))
        # Now confirm extra_kwargs overrides it
        captured.clear()
        fake_litellm = self._stub_litellm(captured)
        opts = model_client.GenerateOptions(
            extra_kwargs={"azure_ad_token_provider": user_provider},
        )
        with patch.object(model_client, "_get_litellm_module", return_value=fake_litellm), \
             patch.object(model_client.azure_auth, "_AZURE_AUTH_MODE", "aad"), \
             patch.object(model_client.azure_auth, "get_azure_token_provider", return_value=lambda: "auto-token"):
            await model_client.generate("azure/gpt-4o-mini", "hi", opts)
        self.assertIs(captured["azure_ad_token_provider"], user_provider)

    # ── azure_ai/* (Azure AI Foundry) injection ────────────────────

    def test_aad_scope_for_model_dispatches_on_family(self) -> None:
        """The scope picker must route each Azure family to the right audience."""
        self.assertEqual(
            model_client._aad_scope_for_model("azure/gpt-4o"),
            model_client.azure_auth.AZURE_OPENAI_SCOPE,
        )
        self.assertEqual(
            model_client._aad_scope_for_model("azure_ai/agents/asst_xxx"),
            model_client.azure_auth.AZURE_FOUNDRY_SCOPE,
        )
        self.assertIsNone(model_client._aad_scope_for_model("openai/gpt-5-mini"))
        self.assertIsNone(model_client._aad_scope_for_model("anthropic/claude-3"))

    async def test_aad_mode_azure_ai_agent_injects_api_key_string(self) -> None:
        """Foundry agents need a static bearer string, not a provider callable.

        LiteLLM's azure_ai/agents provider sets ``Authorization: Bearer
        <api_key>`` directly from ``payload['api_key']``; passing a
        callable there would fail. We must call the provider once per
        request to mint a fresh token.
        """
        captured = await self._run_chat(
            model="azure_ai/agents/asst_xxx",
            mode="aad",
            provider=lambda: "foundry-token",
        )
        self.assertEqual(captured.get("api_key"), "foundry-token")
        self.assertNotIn("azure_ad_token_provider", captured)

    async def test_aad_fallback_azure_ai_agent_injects_api_key_string(self) -> None:
        captured = await self._run_chat(
            model="azure_ai/agents/asst_xxx",
            mode="aad-fallback",
            provider=lambda: "foundry-token",
        )
        self.assertEqual(captured.get("api_key"), "foundry-token")

    async def test_key_mode_azure_ai_agent_no_injection(self) -> None:
        """In key mode we leave the payload alone — LiteLLM resolves auth itself."""
        captured = await self._run_chat(
            model="azure_ai/agents/asst_xxx",
            mode="key",
            provider=lambda: "should-not-be-used",
        )
        self.assertNotIn("api_key", captured)
        self.assertNotIn("azure_ad_token_provider", captured)

    async def test_aad_mode_azure_ai_agent_missing_dep_raises(self) -> None:
        with patch.object(model_client.azure_auth, "_AZURE_AUTH_MODE", "aad"), \
             patch.object(model_client.azure_auth, "get_azure_token_provider", return_value=None), \
             patch.dict("os.environ", {
                 "ASSERT_AZURE_USE_AAD": "1",
                 "AZURE_API_KEY": "",
                 "AZURE_AI_API_KEY": "",
             }, clear=False):
            with self.assertRaises(model_client.LLMAuthError) as ctx:
                await model_client.generate("azure_ai/agents/asst_xxx", "hi")
        self.assertIn("azure-identity", str(ctx.exception))
        self.assertIn("assert-ai[azure-aad]", str(ctx.exception))

    async def test_aad_fallback_azure_ai_agent_missing_dep_silently_skips(self) -> None:
        captured = await self._run_chat(
            model="azure_ai/agents/asst_xxx",
            mode="aad-fallback",
            provider=None,
        )
        self.assertNotIn("api_key", captured)
        self.assertNotIn("azure_ad_token_provider", captured)

    async def test_user_extra_kwargs_api_key_wins_for_azure_ai_agent(self) -> None:
        """Explicit user-supplied api_key must override the auto-minted token."""
        captured: dict[str, object] = {}
        fake_litellm = self._stub_litellm(captured)
        opts = model_client.GenerateOptions(
            extra_kwargs={"api_key": "user-supplied-token"},
        )
        with patch.object(model_client, "_get_litellm_module", return_value=fake_litellm), \
             patch.object(model_client.azure_auth, "_AZURE_AUTH_MODE", "aad"), \
             patch.object(
                 model_client.azure_auth,
                 "get_azure_token_provider",
                 return_value=lambda: "auto-token",
             ), patch.dict("os.environ", {
                 "ASSERT_AZURE_USE_AAD": "1",
                 "AZURE_API_KEY": "",
                 "AZURE_AI_API_KEY": "",
             }, clear=False):
            await model_client.generate("azure_ai/agents/asst_xxx", "hi", opts)
        self.assertEqual(captured["api_key"], "user-supplied-token")

    async def test_azure_api_key_in_env_does_not_block_foundry_aad(self) -> None:
        """Regression: AZURE_API_KEY (Azure OpenAI key) must not gate AAD
        injection for azure_ai/* targets. The two resources have separate
        keys; mixing them silently sent users into key mode for the wrong
        endpoint and surfaced as a 'no bearer token' error from Foundry.

        Real-world setup that hit this: AZURE_API_KEY set so the
        systematize/tester/judge azure/* calls work, no AZURE_AI_API_KEY,
        target.model is azure_ai/agents/<id>. Pre-fix behavior: cached
        mode='key' suppressed AAD injection for the Foundry call too.
        """
        captured: dict[str, object] = {}
        fake_litellm = self._stub_litellm(captured)
        stub_provider = lambda: "foundry-token"
        with patch.object(model_client, "_get_litellm_module", return_value=fake_litellm), \
             patch.object(model_client.azure_auth, "_AZURE_AUTH_MODE", "key"), \
             patch.object(model_client.azure_auth, "get_azure_token_provider", return_value=stub_provider), \
             patch.dict("os.environ", {
                 "AZURE_API_KEY": "sk-azure-openai-key",
                 "AZURE_AI_API_KEY": "",
                 "ASSERT_AZURE_USE_AAD": "",
             }, clear=False):
            await model_client.generate("azure_ai/agents/asst_xxx", "hi")
        # AZURE_AI_API_KEY is unset, so the Foundry-family resolver sees
        # 'aad-fallback' and the new AAD token gets injected.
        self.assertEqual(captured.get("api_key"), "foundry-token")

    async def test_azure_ai_api_key_in_env_skips_foundry_aad_injection(self) -> None:
        """When the user has set AZURE_AI_API_KEY explicitly, the family
        resolver returns 'key' and we leave the payload alone so LiteLLM
        can use the static token from the env. Symmetric to the azure/*
        + AZURE_API_KEY path."""
        captured: dict[str, object] = {}
        fake_litellm = self._stub_litellm(captured)
        with patch.object(model_client, "_get_litellm_module", return_value=fake_litellm), \
             patch.object(model_client.azure_auth, "_AZURE_AUTH_MODE", "aad-fallback"), \
             patch.object(model_client.azure_auth, "get_azure_token_provider", return_value=lambda: "should-not-be-injected"), \
             patch.dict("os.environ", {
                 "AZURE_API_KEY": "",
                 "AZURE_AI_API_KEY": "user-supplied-foundry-key",
                 "ASSERT_AZURE_USE_AAD": "",
             }, clear=False):
            await model_client.generate("azure_ai/agents/asst_xxx", "hi")
        self.assertNotIn("api_key", captured)

    async def test_assert_use_aad_flag_overrides_azure_ai_api_key(self) -> None:
        """Explicit ASSERT_AZURE_USE_AAD=1 wins over a static
        AZURE_AI_API_KEY too, matching the existing azure/* precedence."""
        captured: dict[str, object] = {}
        fake_litellm = self._stub_litellm(captured)
        with patch.object(model_client, "_get_litellm_module", return_value=fake_litellm), \
             patch.object(model_client.azure_auth, "_AZURE_AUTH_MODE", "aad"), \
             patch.object(model_client.azure_auth, "get_azure_token_provider", return_value=lambda: "foundry-token"), \
             patch.dict("os.environ", {
                 "AZURE_API_KEY": "",
                 "AZURE_AI_API_KEY": "ignored-when-flag-set",
                 "ASSERT_AZURE_USE_AAD": "1",
             }, clear=False):
            await model_client.generate("azure_ai/agents/asst_xxx", "hi")
        self.assertEqual(captured.get("api_key"), "foundry-token")


class ClassifyLlmErrorAadHintTest(unittest.TestCase):
    """When AAD is active, the auth-error message must point at RBAC."""

    def _fake_litellm_with_auth_error(self) -> SimpleNamespace:
        class AuthenticationError(Exception):
            pass

        # The other names just need to exist so isinstance checks don't crash.
        return SimpleNamespace(
            AuthenticationError=AuthenticationError,
            PermissionDeniedError=type("PermissionDeniedError", (Exception,), {}),
            RateLimitError=type("RateLimitError", (Exception,), {}),
            BadRequestError=type("BadRequestError", (Exception,), {}),
            NotFoundError=type("NotFoundError", (Exception,), {}),
            APIError=type("APIError", (Exception,), {}),
            APIConnectionError=type("APIConnectionError", (Exception,), {}),
            Timeout=type("Timeout", (Exception,), {}),
            UnprocessableEntityError=type("UnprocessableEntityError", (Exception,), {}),
            APIResponseValidationError=type("APIResponseValidationError", (Exception,), {}),
            ContextWindowExceededError=type("ContextWindowExceededError", (Exception,), {}),
            ContentPolicyViolationError=type("ContentPolicyViolationError", (Exception,), {}),
            UnsupportedParamsError=type("UnsupportedParamsError", (Exception,), {}),
            ImageFetchError=type("ImageFetchError", (Exception,), {}),
            ServiceUnavailableError=type("ServiceUnavailableError", (Exception,), {}),
            InternalServerError=type("InternalServerError", (Exception,), {}),
            BudgetExceededError=type("BudgetExceededError", (Exception,), {}),
        )

    def test_auth_error_message_includes_aad_hint_when_aad_active(self) -> None:
        fake_litellm = self._fake_litellm_with_auth_error()
        exc = fake_litellm.AuthenticationError("401: bad token")
        with patch.object(model_client, "_get_litellm_module", return_value=fake_litellm), \
             patch.object(model_client.azure_auth, "_AZURE_AUTH_MODE", "aad"), \
             patch.object(model_client.azure_auth, "_AZURE_AAD_DEP_MISSING", False):
            wrapped = model_client._classify_llm_error(exc, model="azure/gpt-5.4-mini")
        self.assertIsInstance(wrapped, model_client.LLMAuthError)
        self.assertIn("Cognitive Services OpenAI User", str(wrapped))
        self.assertIn("AZURE_API_KEY", str(wrapped))

    def test_auth_error_message_omits_aad_hint_when_key_mode(self) -> None:
        fake_litellm = self._fake_litellm_with_auth_error()
        exc = fake_litellm.AuthenticationError("401: bad key")
        with patch.object(model_client, "_get_litellm_module", return_value=fake_litellm), \
             patch.object(model_client.azure_auth, "_AZURE_AUTH_MODE", "key"):
            wrapped = model_client._classify_llm_error(exc, model="azure/gpt-5.4-mini")
        self.assertIsInstance(wrapped, model_client.LLMAuthError)
        self.assertNotIn("Cognitive Services OpenAI User", str(wrapped))

    def test_auth_error_message_swaps_to_install_hint_when_dep_missing(self) -> None:
        """In aad-fallback mode with azure-identity missing, point at the install hint."""
        fake_litellm = self._fake_litellm_with_auth_error()
        exc = fake_litellm.AuthenticationError("401: no api key supplied")
        with patch.object(model_client, "_get_litellm_module", return_value=fake_litellm), \
             patch.object(model_client.azure_auth, "_AZURE_AUTH_MODE", "aad-fallback"), \
             patch.object(model_client.azure_auth, "_AZURE_AAD_DEP_MISSING", True):
            wrapped = model_client._classify_llm_error(exc, model="azure/gpt-5.4-mini")
        self.assertIsInstance(wrapped, model_client.LLMAuthError)
        self.assertIn("assert-ai[azure-aad]", str(wrapped))
        # The RBAC hint must not appear in this branch — it would be misleading.
        self.assertNotIn("Cognitive Services OpenAI User", str(wrapped))

    def test_auth_error_omits_azure_hints_for_non_azure_model(self) -> None:
        """A 401 on an ``openai/*`` model must not leak Azure-specific hints
        even when ``ASSERT_AZURE_USE_AAD=1`` is set process-wide for some
        other ``azure/*`` model. Without the model-scope gate, an OpenAI 401
        would advise the user to fix their Azure RBAC role, which is
        actively misleading."""
        fake_litellm = self._fake_litellm_with_auth_error()
        exc = fake_litellm.AuthenticationError("401: invalid api key")
        with patch.object(model_client, "_get_litellm_module", return_value=fake_litellm), \
             patch.object(model_client.azure_auth, "_AZURE_AUTH_MODE", "aad"), \
             patch.object(model_client.azure_auth, "_AZURE_AAD_DEP_MISSING", False):
            wrapped = model_client._classify_llm_error(exc, model="openai/gpt-5.4-mini")
        self.assertIsInstance(wrapped, model_client.LLMAuthError)
        # Neither the RBAC hint nor the install hint should appear on a
        # non-Azure call.
        self.assertNotIn("Cognitive Services OpenAI User", str(wrapped))
        self.assertNotIn("assert-ai[azure-aad]", str(wrapped))
        self.assertNotIn("Azure AD auth is active", str(wrapped))

    def test_auth_error_omits_azure_hints_when_model_not_supplied(self) -> None:
        """Call sites without a model in scope (e.g. user-callable wrappers)
        pass ``model=None`` (the default). The classifier must treat those
        defensively and never append Azure hints."""
        fake_litellm = self._fake_litellm_with_auth_error()
        exc = fake_litellm.AuthenticationError("401")
        with patch.object(model_client, "_get_litellm_module", return_value=fake_litellm), \
             patch.object(model_client.azure_auth, "_AZURE_AUTH_MODE", "aad"), \
             patch.object(model_client.azure_auth, "_AZURE_AAD_DEP_MISSING", False):
            wrapped = model_client._classify_llm_error(exc)
        self.assertIsInstance(wrapped, model_client.LLMAuthError)
        self.assertNotIn("Cognitive Services OpenAI User", str(wrapped))
        self.assertNotIn("assert-ai[azure-aad]", str(wrapped))


if __name__ == "__main__":
    unittest.main()
