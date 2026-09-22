# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import asyncio
import unittest
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from assert_ai.config import parse_model_config, parse_pipeline_config
from assert_ai.core import horse_client, model_client
from assert_ai.core.config_model import BusConfig, HorseConfig, ModelConfig, TargetConfig, ToolsConfig


def _horse_config() -> HorseConfig:
    return HorseConfig(model_name="<horse-virtual-model>", renderer="test-renderer", top_p=0.8)


def _message(content: str, *, role: str = "assistant", channel: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(role=role, content=content, channel=SimpleNamespace(value=channel))


class HorseConfigTest(unittest.TestCase):
    def test_parse_horse_config(self) -> None:
        config = parse_model_config(
            {
                "name": "horse/label",
                "max_tokens": 128,
                "temperature": 0.0,
                "horse": {"model_name": " <horse-virtual-model> ", "renderer": " test-renderer "},
            },
            field_name="test.model",
        )
        self.assertIsNone(config.bus)
        self.assertEqual(config.horse.model_name, "<horse-virtual-model>")
        self.assertEqual(config.horse.renderer, "test-renderer")
        self.assertEqual(config.horse.top_p, 1.0)
        self.assertEqual(config.max_tokens, 128)
        self.assertEqual(config.temperature, 0.0)

    def test_rejects_invalid_horse_configs(self) -> None:
        valid = asdict(_horse_config())
        cases = [
            (None, "must be a mapping"),
            ("horse", "must be a mapping"),
            ({}, "model_name is required"),
            ({**valid, "model_name": " "}, "model_name is required"),
            ({**valid, "model_name": 123}, "model_name must be a string"),
            ({"model_name": valid["model_name"]}, "renderer is required"),
            ({**valid, "renderer": " "}, "renderer is required"),
            ({**valid, "top_p": 0}, "top_p must be"),
            ({**valid, "top_p": 1.1}, "top_p must be"),
            ({**valid, "top_p": float("nan")}, "top_p must be"),
        ]
        cases.extend(
            ({**valid, key: "not-supported"}, "unsupported field")
            for key in ("snapshot", "user", "bus_line", "qos_type", "topic_mode_or_user", "bus_session_id")
        )
        for raw, error in cases:
            with self.subTest(raw=raw), self.assertRaisesRegex(ValueError, error):
                parse_model_config({"name": "horse/label", "horse": raw}, field_name="test.model")

    def test_bus_and_horse_are_mutually_exclusive(self) -> None:
        bus = BusConfig(snapshot="az://container/snapshot", user="test", renderer="test-renderer")
        horse = _horse_config()
        with self.assertRaisesRegex(ValueError, "mutually exclusive"):
            parse_model_config(
                {"name": "label", "bus": asdict(bus), "horse": asdict(horse)},
                field_name="test.model",
            )
        with self.assertRaisesRegex(ValueError, "mutually exclusive"):
            ModelConfig(name="label", bus=bus, horse=horse)
        with self.assertRaisesRegex(ValueError, "mutually exclusive"):
            model_client.GenerateOptions(bus=bus, horse=horse)

    def test_target_tools_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "target.tools is not supported with model.horse"):
            TargetConfig(
                model=ModelConfig(name="horse/label", horse=_horse_config()),
                tools=ToolsConfig(module="example.tools"),
            )

    def test_reasoning_effort_is_rejected_instead_of_ignored(self) -> None:
        with self.assertRaisesRegex(ValueError, "reasoning_effort is not supported with model.horse"):
            parse_model_config(
                {"name": "horse/label", "reasoning_effort": "high", "horse": asdict(_horse_config())},
                field_name="model",
            )

    def test_pipeline_preserves_distinct_target_tester_and_judge_routes(self) -> None:
        def model(label: str) -> dict:
            return {
                "name": f"horse/{label}",
                "horse": {"model_name": f"<{label}-virtual-model>", "renderer": f"{label}-renderer"},
            }

        pipeline = parse_pipeline_config({
            "pipeline": {
                "inference": {
                    "target": {"model": model("target")},
                    "tester": {"model": model("tester")},
                },
                "judge": {"model": model("judge")},
            },
        })
        self.assertEqual(pipeline.target.model.horse.model_name, "<target-virtual-model>")
        self.assertEqual(pipeline.evaluation.tester.model.horse.model_name, "<tester-virtual-model>")
        self.assertEqual(pipeline.evaluation.judge.model.horse.model_name, "<judge-virtual-model>")


class HorseClientTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.config = _horse_config()
        self.choice = SimpleNamespace(
            error=None,
            finish_reason="stop",
            get_messages=lambda: [
                _message("first thought", channel="analysis"),
                _message("second thought", channel="analysis"),
                _message("old answer", channel="final"),
                _message("answer", channel="final"),
            ],
        )
        self.completer = SimpleNamespace(
            async_completion=AsyncMock(return_value=SimpleNamespace(choices=[self.choice])),
            close=Mock(),
        )
        self.token_type = SimpleNamespace(Config=Mock(return_value=object()))
        self.message_type = SimpleNamespace(
            Config=Mock(return_value=SimpleNamespace(build=Mock(return_value=self.completer))),
        )
        chat = SimpleNamespace(
            Message=SimpleNamespace(
                system=lambda content: _message(content, role="system"),
                user=lambda content: _message(content, role="user"),
                assistant=lambda content, channel: _message(content, channel=channel),
            ),
            Conversation=lambda *, messages: SimpleNamespace(messages=messages),
        )
        stack = (self.token_type, chat, lambda message: message.content, self.message_type)
        loader = patch.object(horse_client, "_load_horse_stack", return_value=stack)
        loader.start()
        self.addCleanup(loader.stop)

    async def test_complete_selects_horse_and_preserves_messages_and_channels(self) -> None:
        result = await horse_client.complete(
            [
                {"role": "system", "content": "instructions"},
                {"role": "user", "content": [{"text": "hello"}, " world"]},
                {"role": "assistant", "content": "history"},
            ],
            config=self.config,
            max_tokens=64,
            temperature=0.0,
        )
        self.token_type.Config.assert_called_once_with(
            use_horse=True, model_name=self.config.model_name,
        )
        self.message_type.Config.assert_called_once_with(
            token_completer_config=self.token_type.Config.return_value,
            renderer=self.config.renderer,
            completion_params={"top_p": 0.8, "max_tokens": 64, "temperature": 0.0},
        )
        [conversation] = self.completer.async_completion.call_args.args[0]
        self.assertEqual([message.role for message in conversation.messages], ["system", "user", "assistant"])
        self.assertEqual(conversation.messages[1].content, "hello world")
        self.assertEqual(conversation.messages[2].channel.value, "final")
        self.assertEqual(result.text, "answer")
        self.assertEqual(result.reasoning, "first thought\nsecond thought")
        self.assertEqual(result.finish_reason, "stop")
        self.assertEqual(result.raw["messages"][-1], {"channel": "final", "content": "answer"})
        self.completer.close.assert_called_once_with()

    async def test_complete_omits_unset_sampling_options(self) -> None:
        await horse_client.complete([], config=self.config, max_tokens=None, temperature=None)
        self.assertEqual(self.message_type.Config.call_args.kwargs["completion_params"], {"top_p": 0.8})

    async def test_complete_preserves_final_prefix_fallback(self) -> None:
        for text in ("final: answer", "channel: final\nanswer"):
            with self.subTest(text=text):
                self.choice.get_messages = lambda: [_message(text)]
                self.choice.finish_reason = None
                result = await horse_client.complete([], config=self.config, max_tokens=64, temperature=None)
                self.assertEqual(result.text, "answer")
                self.assertEqual(result.finish_reason, "stop")

    async def test_complete_surfaces_empty_or_failed_outputs(self) -> None:
        cases = [
            ([], "no choices"),
            ([SimpleNamespace(error="engine unavailable")], "engine unavailable"),
            ([SimpleNamespace(error=None, get_messages=lambda: [])], "no messages"),
            ([SimpleNamespace(error=None, get_messages=lambda: [_message(" ")])], "empty response"),
        ]
        for choices, error in cases:
            with self.subTest(error=error):
                self.completer.close.reset_mock()
                self.completer.async_completion.return_value = SimpleNamespace(choices=choices)
                with self.assertRaisesRegex(RuntimeError, error):
                    await horse_client.complete([], config=self.config, max_tokens=None, temperature=None)
                self.completer.close.assert_called_once_with()

    async def test_complete_closes_on_failure_and_cancellation(self) -> None:
        for error in (RuntimeError("engine unavailable"), asyncio.CancelledError()):
            with self.subTest(error=type(error)):
                self.completer.close.reset_mock()
                self.completer.async_completion.side_effect = error
                with self.assertRaises(type(error)):
                    await horse_client.complete([], config=self.config, max_tokens=None, temperature=None)
                self.completer.close.assert_called_once_with()

    async def test_unsupported_role_closes_completer_without_sending(self) -> None:
        with self.assertRaisesRegex(ValueError, "Horse model transport does not support message role 'tool'"):
            await horse_client.complete(
                [{"role": "tool", "content": "result"}],
                config=self.config,
                max_tokens=None,
                temperature=None,
            )
        self.completer.async_completion.assert_not_called()
        self.completer.close.assert_called_once_with()


class HorseDependencyTest(unittest.TestCase):
    def test_missing_optional_dependency_has_actionable_error(self) -> None:
        with (
            patch.dict("sys.modules", {"ev3_token_completer": None}),
            self.assertRaisesRegex(RuntimeError, "Horse.*ev3_token_completer.*PYTHONPATH") as raised,
        ):
            horse_client._load_horse_stack()
        self.assertIsInstance(raised.exception.__cause__, ImportError)


class HorseModelClientTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.config = _horse_config()
        self.completion = horse_client.HorseCompletion(
            text='{"verdict": "pass"}',
            reasoning="reasoning",
            finish_reason="stop",
            raw={"messages": []},
        )

    async def test_text_and_structured_generation_route_only_to_horse(self) -> None:
        for structured in (False, True):
            with (
                self.subTest(structured=structured),
                patch.object(horse_client, "complete", return_value=self.completion) as complete,
                patch("assert_ai.core.bus_client.complete") as bus_complete,
                patch.object(model_client, "_get_litellm_module") as get_litellm,
                model_client.track_usage() as usage,
            ):
                options = model_client.GenerateOptions(horse=self.config, max_tokens=64, temperature=0.0)
                if structured:
                    response = await model_client.generate_structured(
                        "horse/label", "hello", options=options,
                        schema_name="verdict",
                        json_schema={"type": "object", "properties": {"verdict": {"type": "string"}}},
                    )
                    self.assertEqual(response.request_payload["response_format"]["type"], "json_schema")
                else:
                    response = await model_client.generate("horse/label", "hello", options)
                complete.assert_awaited_once_with(
                    [{"role": "user", "content": "hello"}],
                    config=self.config, max_tokens=64, temperature=0.0,
                )
                bus_complete.assert_not_called()
                get_litellm.assert_not_called()
                self.assertEqual(response.text, self.completion.text)
                self.assertEqual(response.parsed, {"verdict": "pass"})
                self.assertEqual(response.reasoning, "reasoning")
                self.assertEqual(response.api_mode, "horse")
                self.assertEqual(response.model, "horse/label")
                self.assertEqual(response.request_payload["horse"], asdict(self.config))
                self.assertNotIn("bus", response.request_payload)
                self.assertEqual(usage.calls, 1)

    async def test_max_output_tokens_are_forwarded(self) -> None:
        with patch.object(horse_client, "complete", return_value=self.completion) as complete:
            await model_client.generate(
                "horse/label", "hello",
                model_client.GenerateOptions(horse=self.config, max_output_tokens=96),
            )
        self.assertEqual(complete.call_args.kwargs["max_tokens"], 96)

    async def test_horse_errors_do_not_fall_back_to_bus_or_litellm(self) -> None:
        with (
            patch.object(horse_client, "complete", side_effect=RuntimeError("engine unavailable")),
            patch("assert_ai.core.bus_client.complete") as bus_complete,
            patch.object(model_client, "_get_litellm_module") as get_litellm,
            self.assertRaisesRegex(model_client.LLMProviderError, "Horse.*engine unavailable"),
        ):
            await model_client.generate("horse/label", "hello", model_client.GenerateOptions(horse=self.config))
        bus_complete.assert_not_called()
        get_litellm.assert_not_called()

    async def test_horse_timeout_cancels_request(self) -> None:
        cancelled = asyncio.Event()

        async def complete(*args, **kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        with (
            patch.object(horse_client, "complete", new=complete),
            self.assertRaises(model_client.LLMProviderError),
        ):
            await model_client.generate(
                "horse/label", "hello", model_client.GenerateOptions(horse=self.config, timeout_s=0.01),
            )
        self.assertTrue(cancelled.is_set())

    async def test_horse_rejects_tools_and_web_search_before_dispatch(self) -> None:
        with patch.object(horse_client, "complete") as complete:
            with self.assertRaisesRegex(ValueError, "Horse.*target.tools"):
                await model_client.generate_with_tools(
                    "horse/label", "hello", tools=[], options=model_client.GenerateOptions(horse=self.config),
                )
            for structured in (False, True):
                with self.subTest(structured=structured), self.assertRaisesRegex(ValueError, "Horse.*web_search"):
                    options = model_client.GenerateOptions(horse=self.config, web_search=True)
                    if structured:
                        await model_client.generate_structured(
                            "horse/label", "hello", schema_name="test", json_schema={}, options=options,
                        )
                    else:
                        await model_client.generate("horse/label", "hello", options)
        complete.assert_not_called()

    async def test_horse_rejects_reasoning_effort_before_dispatch(self) -> None:
        with (
            patch.object(horse_client, "complete") as complete,
            self.assertRaisesRegex(ValueError, "Horse.*reasoning_effort"),
        ):
            await model_client.generate(
                "horse/label", "hello", model_client.GenerateOptions(horse=self.config, reasoning_effort="high"),
            )
        complete.assert_not_called()
