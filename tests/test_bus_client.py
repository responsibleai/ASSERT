# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from assert_ai.core import bus_client
from assert_ai.core.config_model import BusConfig


class _FakeMessage:
    def __init__(self, role: str, content: str, channel: str | None = None) -> None:
        self.role = role
        self.content = content
        self.channel = SimpleNamespace(value=channel) if channel else None


class _FakeChat:
    class Message:
        @staticmethod
        def system(content: str) -> _FakeMessage:
            return _FakeMessage("system", content)

        @staticmethod
        def user(content: str) -> _FakeMessage:
            return _FakeMessage("user", content)

        @staticmethod
        def assistant(content: str, channel: str | None = None) -> _FakeMessage:
            return _FakeMessage("assistant", content, channel)

    class Conversation:
        def __init__(self, *, messages: list[_FakeMessage]) -> None:
            self.messages = messages


class BusClientTest(unittest.IsolatedAsyncioTestCase):
    async def test_complete_uses_round_robin_by_user_and_extracts_channels(self) -> None:
        captured: dict[str, Any] = {}
        round_robin_by_user = object()

        class FakeBusTokenCompleter:
            @staticmethod
            def Config(**kwargs: object) -> object:
                captured["bus_config"] = kwargs
                return kwargs

        output_messages = [
            _FakeMessage("assistant", "private reasoning", "analysis"),
            _FakeMessage("assistant", "answer", "final"),
        ]
        choice = SimpleNamespace(
            error=None,
            finish_reason="stop",
            get_messages=lambda: output_messages,
        )

        class FakeCompleter:
            closed = False

            async def async_completion(self, conversations: list[object]) -> object:
                captured["conversations"] = conversations
                return SimpleNamespace(choices=[choice])

            def close(self) -> None:
                self.closed = True

        fake_completer = FakeCompleter()

        class FakeTokenMessageCompleter:
            class Config:
                def __init__(self, **kwargs: object) -> None:
                    captured["message_config"] = kwargs

                def build(self) -> FakeCompleter:
                    return fake_completer

        stack = (
            SimpleNamespace(ROUND_ROBIN_BY_USER=round_robin_by_user),
            FakeBusTokenCompleter,
            _FakeChat,
            lambda message: message.content,
            FakeTokenMessageCompleter,
        )
        config = BusConfig(
            snapshot="az://container/models/snapshot",
            user="grader",
            renderer="harmony-test",
        )

        with patch.object(bus_client, "_load_bus_stack", return_value=stack):
            result = await bus_client.complete(
                [
                    {"role": "system", "content": "judge"},
                    {"role": "user", "content": "sample"},
                    {"role": "assistant", "content": "history"},
                ],
                config=config,
                max_tokens=256,
                temperature=0.0,
            )

        self.assertEqual(captured["bus_config"]["qos_type"], round_robin_by_user)
        self.assertEqual(captured["bus_config"]["topic_or_snapshot"], config.snapshot)
        self.assertEqual(captured["bus_config"]["topic_mode_or_user"], config.user)
        self.assertEqual(
            captured["message_config"]["completion_params"],
            {"top_p": 1.0, "max_tokens": 256, "temperature": 0.0},
        )
        conversation = captured["conversations"][0]
        self.assertEqual([message.role for message in conversation.messages], ["system", "user", "assistant"])
        self.assertEqual(result.text, "answer")
        self.assertEqual(result.reasoning, "private reasoning")
        self.assertTrue(fake_completer.closed)

    async def test_complete_accepts_prefixed_final_fallback(self) -> None:
        choice = SimpleNamespace(
            error=None,
            finish_reason=None,
            get_messages=lambda: [_FakeMessage("assistant", "final: fallback answer")],
        )

        class FakeCompleter:
            async def async_completion(self, _conversations: list[object]) -> object:
                return SimpleNamespace(choices=[choice])

            def close(self) -> None:
                return None

        class FakeTokenMessageCompleter:
            class Config:
                def __init__(self, **_kwargs: object) -> None:
                    return None

                def build(self) -> FakeCompleter:
                    return FakeCompleter()

        stack = (
            SimpleNamespace(ROUND_ROBIN_BY_USER="round-robin"),
            SimpleNamespace(Config=lambda **_kwargs: object()),
            _FakeChat,
            lambda message: message.content,
            FakeTokenMessageCompleter,
        )
        config = BusConfig(
            snapshot="az://container/models/snapshot",
            user="grader",
            renderer="harmony-test",
        )

        with patch.object(bus_client, "_load_bus_stack", return_value=stack):
            result = await bus_client.complete(
                [{"role": "user", "content": "sample"}],
                config=config,
                max_tokens=64,
                temperature=None,
            )

        self.assertEqual(result.text, "fallback answer")
        self.assertEqual(result.finish_reason, "stop")