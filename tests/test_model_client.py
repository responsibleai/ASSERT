import unittest
from types import SimpleNamespace
from unittest.mock import patch

from p2m.core import model_client


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


if __name__ == "__main__":
    unittest.main()
