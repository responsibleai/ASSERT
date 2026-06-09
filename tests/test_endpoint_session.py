# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import json
import unittest

from assert_ai.core.model_client import Message
from assert_ai.core.session import HTTPEndpointSession


class _FakeResponse:
    def __init__(self, payload: dict, *, status: int = 200, content_type: str = "application/json") -> None:
        self._payload = payload
        self.status = status
        self.content_type = content_type

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    async def json(self):
        return self._payload


class _FakeClientSession:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.requests: list[dict] = []
        self.closed = False

    def post(self, url: str, *, json: dict, headers: dict | None = None):
        self.requests.append({"url": url, "json": json, "headers": headers or {}})
        return _FakeResponse(self.payload)

    async def close(self) -> None:
        self.closed = True


class HTTPEndpointSessionTest(unittest.IsolatedAsyncioTestCase):
    async def test_default_endpoint_protocol_preserves_legacy_payload_and_response(self) -> None:
        client = _FakeClientSession({"response": "legacy answer"})
        session = HTTPEndpointSession(endpoint="http://localhost:8787/chat")
        setattr(session, "_aiohttp", object())
        setattr(session, "_session", client)

        result = await session.run_turn([Message(role="user", content="hello")])

        self.assertEqual(result.text, "legacy answer")
        self.assertEqual(
            client.requests[0]["json"],
            {"message": "hello", "history": [{"role": "user", "content": "hello"}]},
        )
        self.assertEqual(result.interaction_messages[-1]["content"], "legacy answer")

    async def test_openai_chat_protocol_posts_messages_and_extracts_tool_calls(self) -> None:
        payload = {
            "id": "chatcmpl_test",
            "model": "custom-agent",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": "I will check that.",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "lookup", "arguments": json.dumps({"query": "x"})},
                            }
                        ],
                    },
                }
            ],
        }
        client = _FakeClientSession(payload)
        session = HTTPEndpointSession(
            endpoint="http://localhost:8000/v1/chat/completions",
            protocol="openai_chat",
            model="custom-agent",
            headers={"Authorization": "Bearer secret-token"},
        )
        setattr(session, "_aiohttp", object())
        setattr(session, "_session", client)

        result = await session.run_turn(
            [
                Message(role="system", content="You are concise."),
                Message(role="user", content="Use the lookup tool."),
            ]
        )

        self.assertEqual(result.text, "I will check that.")
        self.assertEqual(client.requests[0]["json"]["model"], "custom-agent")
        self.assertEqual(
            client.requests[0]["json"]["messages"],
            [
                {"role": "system", "content": "You are concise."},
                {"role": "user", "content": "Use the lookup tool."},
            ],
        )
        assistant_turn = result.interaction_messages[-1]
        self.assertEqual(assistant_turn["tool_calls"][0]["function"], "lookup")
        self.assertEqual(assistant_turn["tool_calls"][0]["arguments"], {"query": "x"})
        self.assertEqual(result.finish_reason, "tool_calls")
        raw = result.raw or {}
        self.assertEqual(raw["response"]["model"], "custom-agent")
        self.assertEqual(raw["response"]["usage"]["total_tokens"], 15)
        self.assertNotIn("secret-token", json.dumps(raw))

    async def test_endpoint_events_are_recorded_as_tool_interaction_messages(self) -> None:
        client = _FakeClientSession(
            {
                "response": "The file says hello.",
                "events": [
                    {
                        "role": "tool_call",
                        "tool_name": "read_file",
                        "tool_args": {"path": "README.md"},
                        "tool_call_id": "call_read",
                    },
                    {
                        "role": "tool_result",
                        "tool_name": "read_file",
                        "content": "hello",
                        "tool_call_id": "call_read",
                    },
                ],
                "metadata": {"runtime": "test-agent"},
            }
        )
        session = HTTPEndpointSession(endpoint="http://localhost:8787/chat")
        setattr(session, "_aiohttp", object())
        setattr(session, "_session", client)

        result = await session.run_turn([Message(role="user", content="read it")])

        self.assertEqual(result.text, "The file says hello.")
        self.assertEqual(result.interaction_messages[1]["tool_calls"][0]["function"], "read_file")
        self.assertEqual(result.interaction_messages[2]["function"], "read_file")
        self.assertEqual(result.interaction_messages[3]["content"], "The file says hello.")
        raw = result.raw or {}
        self.assertEqual(raw["metadata"], {"runtime": "test-agent"})
    async def test_endpoint_response_raw_payload_is_sanitized(self) -> None:
        client = _FakeClientSession(
            {
                "response": "done",
                "metadata": {
                    "runtime": "test-agent",
                    "api_key": "sk-test-secret-value",
                    "nested": {"Authorization": "Bearer secret-token"},
                },
            }
        )
        session = HTTPEndpointSession(endpoint="http://localhost:8787/chat")
        setattr(session, "_aiohttp", object())
        setattr(session, "_session", client)

        result = await session.run_turn([Message(role="user", content="hello")])

        raw_json = json.dumps(result.raw or {})
        self.assertNotIn("sk-test-secret-value", raw_json)
        self.assertNotIn("Bearer secret-token", raw_json)
        self.assertIn("[REDACTED]", raw_json)


if __name__ == "__main__":
    unittest.main()
