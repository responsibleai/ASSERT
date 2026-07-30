# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""HTTP endpoint evidence normalization.

An endpoint target is the generic boundary for a separately running sandbox. It
must preserve adapter-shaped tool events returned alongside the final response;
otherwise mediation works but the judge sees only text and cannot score the
attempted action.
"""
from __future__ import annotations

import asyncio

import aiohttp

from assert_ai.core.model_client import Message
from assert_ai.core.session import HTTPEndpointSession


class _Response:
    def __init__(self, payload):
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def raise_for_status(self):
        return None

    async def json(self):
        return self.payload


class _Client:
    def __init__(self, payload):
        self.payload = payload
        self.posts = []

    def post(self, endpoint, *, json, headers):
        self.posts.append((endpoint, json, headers))
        return _Response(self.payload)


async def _run(payload):
    session = HTTPEndpointSession(endpoint="http://localhost:8080/chat")
    client = _Client(payload)
    setattr(session, "_aiohttp", aiohttp)
    setattr(session, "_session", client)
    result = await session.run_turn([Message(role="user", content="restore the line")])
    return result, client


def test_endpoint_promotes_tool_events_to_judge_visible_messages():
    result, client = asyncio.run(_run({
        "response": "The line is restored.",
        "events": [
            {
                "role": "tool_call",
                "tool_name": "resume_line",
                "tool_args": {"line_id": "L1002"},
                "tool_call_id": "tc-1",
                "content": "",
            },
            {
                "role": "tool_result",
                "tool_name": "resume_line",
                "tool_args": {"line_id": "L1002"},
                "tool_call_id": "tc-1",
                "content": '{"mode":"pass","real_executed":true}',
            },
        ],
        # Deliberately sensitive-looking diagnostics: endpoint normalization must
        # not copy the complete payload into artifacts.
        "internal_diagnostics": {"token": "secret-value"},
    }))

    assert result.text == "The line is restored."
    assert [m["role"] for m in result.interaction_messages] == ["user", "assistant", "tool", "assistant"]
    tool_call = result.interaction_messages[1]
    tool_result = result.interaction_messages[2]
    assert tool_call["tool_calls"] == [{
        "id": "tc-1",
        "function": "resume_line",
        "arguments": {"line_id": "L1002"},
    }]
    assert tool_result["function"] == "resume_line"
    assert tool_result["tool_call_id"] == "tc-1"
    assert result.interaction_messages[-1]["content"] == "The line is restored."
    assert result.raw == {"endpoint": "http://localhost:8080/chat"}
    assert "secret-value" not in str(result.interaction_messages)
    assert client.posts[0][1] == {
        "message": "restore the line",
        "history": [{"role": "user", "content": "restore the line"}],
    }


def test_endpoint_does_not_duplicate_final_assistant_event():
    result, _ = asyncio.run(_run({
        "response": "Done.",
        "events": [{"role": "assistant", "content": "Done."}],
    }))
    assistants = [m for m in result.interaction_messages if m["role"] == "assistant"]
    assert [m["content"] for m in assistants] == ["Done."]


def test_endpoint_without_events_keeps_black_box_behavior():
    result, _ = asyncio.run(_run({"response": "Plain answer."}))
    assert result.text == "Plain answer."
    assert [(m["role"], m["content"]) for m in result.interaction_messages] == [
        ("user", "restore the line"),
        ("assistant", "Plain answer."),
    ]


def test_endpoint_rejects_non_object_json():
    try:
        asyncio.run(_run(["not", "an", "object"]))
    except RuntimeError as exc:
        assert "non-object JSON" in str(exc)
    else:
        raise AssertionError("non-object endpoint response must fail")
