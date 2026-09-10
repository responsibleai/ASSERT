# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Local HTTP server used by the offline Langfuse integration tests."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


@dataclass
class RecordedRequest:
    method: str
    path: str
    headers: dict[str, str]
    body: dict[str, Any]


@dataclass
class FakeLangfuse:
    base_url: str = ""
    requests: list[RecordedRequest] = field(default_factory=list)
    response_status: int = 200
    response_body: bytes = b'{"ok":true}'
    response_headers: dict[str, str] = field(default_factory=dict)


@contextmanager
def fake_langfuse_server() -> Iterator[FakeLangfuse]:
    state = FakeLangfuse()

    class Handler(BaseHTTPRequestHandler):
        def _record_request(self, body: dict[str, Any]) -> None:
            state.requests.append(
                RecordedRequest(
                    method=self.command,
                    path=self.path,
                    headers={key.lower(): value for key, value in self.headers.items()},
                    body=body,
                )
            )

        def _send_response(self) -> None:
            self.send_response(state.response_status)
            self.send_header("Content-Type", "application/json")
            for name, value in state.response_headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(state.response_body)

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            self._record_request(body)
            self._send_response()

        def do_GET(self) -> None:  # noqa: N802
            self._record_request({})
            self._send_response()

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state.base_url = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


__all__ = ["FakeLangfuse", "RecordedRequest", "fake_langfuse_server"]
