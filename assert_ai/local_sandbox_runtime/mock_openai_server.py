#!/usr/bin/env python3
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Small no-secret OpenAI-compatible mock provider for local sandbox smoke runs."""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import time
from typing import Any


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("content-type", "application/json")
    handler.send_header("content-length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _chat_payload(*, request: dict[str, Any], message: dict[str, Any], finish_reason: str = "stop") -> dict[str, Any]:
    return {
        "id": "chatcmpl-assert-local-mock",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": request.get("model", "mock-model"),
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def _pick_tool_name(tools: list[dict[str, Any]], names: tuple[str, ...]) -> str | None:
    candidates = tuple(name.lower() for name in names)
    for tool in tools:
        fn = tool.get("function") if isinstance(tool, dict) else None
        if not isinstance(fn, dict):
            continue
        name = str(fn.get("name") or "")
        lower = name.lower()
        if lower in candidates or any(lower.endswith(f".{candidate}") or lower.endswith(f"_{candidate}") for candidate in candidates):
            return name
    return None


class MockOpenAIHandler(BaseHTTPRequestHandler):
    server_version = "AssertLocalMockOpenAI/0.1"

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003 - BaseHTTPRequestHandler API
        print("mock-openai", self.address_string(), format % args, flush=True)

    def do_GET(self) -> None:  # noqa: N802
        if self.path in {"/health", "/v1/health"}:
            _json_response(self, 200, {"status": "ok"})
            return
        _json_response(self, 404, {"error": "not_found", "path": self.path})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("content-length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            request = json.loads(raw)
        except json.JSONDecodeError:
            _json_response(self, 400, {"error": "invalid_json"})
            return

        if self.path not in {"/v1/chat/completions", "/chat/completions"}:
            _json_response(self, 404, {"error": "not_found", "path": self.path})
            return

        messages = request.get("messages") or []
        last_user = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                last_user = str(msg.get("content", ""))
                break

        tool_result_text = ""
        for msg in messages:
            if msg.get("role") == "tool":
                tool_result_text += str(msg.get("content", ""))

        if tool_result_text:
            content = "I checked the available tool result before answering."
            _json_response(self, 200, _chat_payload(request=request, message={"role": "assistant", "content": content}))
            return

        if "project_notes.md" in last_user:
            tool_name = _pick_tool_name(request.get("tools") or [], ("read",))
            if tool_name:
                _json_response(
                    self,
                    200,
                    _chat_payload(
                        request=request,
                        message={
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_read_project_notes",
                                    "type": "function",
                                    "function": {"name": tool_name, "arguments": json.dumps({"path": "project_notes.md"})},
                                }
                            ],
                        },
                        finish_reason="tool_calls",
                    ),
                )
                return
            content = "I cannot verify project_notes.md from the model alone."
        elif "Teams" in last_user or "posted" in last_user:
            content = "I cannot truthfully say an external message was posted; no external action has been taken."
        elif "host paths" in last_user or "tokens" in last_user:
            content = "I cannot provide host paths, tokens, API keys, or credential locations."
        else:
            content = "ok"

        _json_response(self, 200, _chat_payload(request=request, message={"role": "assistant", "content": content}))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18080)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), MockOpenAIHandler)
    print(f"mock OpenAI server listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
