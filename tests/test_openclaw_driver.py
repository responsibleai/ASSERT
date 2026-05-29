# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import unittest
from unittest import mock

from examples.prompt_agents.openclaw import (
    Adapter,
    _text_from_payload,
    _text_from_events,
)


class ExtractHelpersTest(unittest.TestCase):
    def test_text_from_payload_joins_non_empty(self) -> None:
        payload = {
            "payloads": [
                {"text": "First update"},
                {"text": ""},
                {"text": "Final answer"},
            ]
        }
        self.assertEqual(
            _text_from_payload(payload),
            "First update\n\nFinal answer",
        )

    def test_text_from_events_ignores_tool_calls_and_thinking(self) -> None:
        events = [
            {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "internal"},
                        {"type": "text", "text": "Checking files"},
                        {"type": "toolCall", "name": "exec", "arguments": {"command": "ls"}},
                    ],
                },
            },
            {
                "type": "message",
                "message": {
                    "role": "toolResult",
                    "toolName": "exec",
                    "content": [{"type": "text", "text": "file_a"}],
                },
            },
            {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Done."}],
                },
            },
        ]
        self.assertEqual(
            _text_from_events(events),
            "Checking files\n\nDone.",
        )


class AdapterLifecycleTest(unittest.TestCase):
    def test_open_sets_project_and_starts_container(self) -> None:
        adapter = Adapter({})

        compose_results = [
            mock.Mock(stdout="", returncode=0),
            mock.Mock(stdout="container-123\n", returncode=0),
        ]

        with mock.patch.object(adapter, "_compose", side_effect=compose_results) as compose:
            adapter.open()

        self.assertTrue(adapter._project.startswith("openclaw-"))
        self.assertEqual(adapter._container, "container-123")
        compose.assert_has_calls(
            [
                mock.call(["up", "-d", "--wait", "openclaw-gateway"]),
                mock.call(["ps", "-q", "openclaw-gateway"]),
            ]
        )
