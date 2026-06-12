#!/usr/bin/env python3
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""HTTP endpoint bridge for an OpenClaw Docker sandbox.

The bridge exposes ASSERT's simple endpoint protocol on localhost while using the
RAMPART OpenClaw adapter under the hood for sandbox communication and tool-call
evidence.
"""

from __future__ import annotations

import argparse
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

_SAFE_METADATA_KEYS = {
    "run_id",
    "model",
    "provider",
    "duration_ms",
    "usage",
    "openclaw_session_id",
    "tool_call_sequence",
    "openclaw_version",
    "node_version",
    "llm_request_failed",
}


def _safe_response_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {key: metadata[key] for key in _SAFE_METADATA_KEYS if key in metadata}


def _ensure_rampart_openclaw_importable(rampart_root: Path) -> None:
    src = rampart_root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


async def _seed_workspace_async(adapter: Any, workspace: Path, *, max_files: int = 50, max_bytes: int = 1_000_000) -> None:
    client = adapter.sandbox_client
    copied_files = 0
    copied_bytes = 0
    for path in sorted(workspace.rglob("*")):
        if copied_files >= max_files or copied_bytes >= max_bytes:
            break
        if not path.is_file():
            continue
        rel = path.relative_to(workspace).as_posix()
        if rel.startswith(".") or any(part in {"tmp", "node_modules", ".git"} for part in Path(rel).parts):
            continue
        size = path.stat().st_size
        if size > 512_000 or copied_bytes + size > max_bytes:
            continue
        data = path.read_bytes()
        parent = Path(rel).parent.as_posix()
        await client.exec_async(
            command=f"mkdir -p /home/agent/workspace/{parent} && cat > /home/agent/workspace/{rel}",
            stdin_data=data,
            timeout=30,
        )
        copied_files += 1
        copied_bytes += size


class OpenClawEndpoint:
    def __init__(self, *, sandbox_name: str, workspace: Path, rampart_root: Path, docker_command: str) -> None:
        self.sandbox_name = sandbox_name
        self.workspace = workspace
        self.rampart_root = rampart_root
        self.docker_command = docker_command
        _ensure_rampart_openclaw_importable(rampart_root)
        self.openclaw_mod = importlib.import_module("openclaw")
        self.rampart_types = importlib.import_module("rampart.core.types")
        self.adapter = self.openclaw_mod.OpenClawAdapter(sandbox_name=sandbox_name, docker_command=docker_command)
        asyncio.run(_seed_workspace_async(self.adapter, workspace))

    async def _send_async(self, message: str) -> dict[str, Any]:
        request_type = self.rampart_types.Request
        async with await self.adapter.create_session_async() as session:
            response = await session.send_async(request_type(prompt=message))
        events: list[dict[str, Any]] = []
        for call in response.tool_calls:
            tool_name = getattr(call, "name", "")
            events.append({"role": "tool_call", "tool_name": tool_name, "tool_args": getattr(call, "arguments", {})})
            tool_result = getattr(call, "result", None)
            if tool_result is not None:
                events.append({"role": "tool_result", "tool_name": tool_name, "content": tool_result})
        return {"response": response.text, "events": events, "metadata": _safe_response_metadata(response.metadata)}

    def send(self, message: str) -> dict[str, Any]:
        return asyncio.run(self._send_async(message))


def make_handler(endpoint: OpenClawEndpoint) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "assert-openclaw-endpoint/0.1"

        def _write_json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003 - BaseHTTPRequestHandler API
            print(f"{self.address_string()} - {format % args}", flush=True)

        def do_GET(self) -> None:  # noqa: N802
            if self.path.rstrip("/") == "/health":
                self._write_json(200, {"status": "ok", "sandbox_name": endpoint.sandbox_name})
                return
            self._write_json(404, {"error": "not_found"})

        def do_POST(self) -> None:  # noqa: N802
            try:
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length).decode("utf-8")
                payload = json.loads(raw or "{}")
                message = str(payload.get("message") or payload.get("prompt") or "")
                if not message:
                    self._write_json(400, {"error": "message_required"})
                    return
                self._write_json(200, endpoint.send(message))
            except Exception as exc:  # pragma: no cover - integration defensive path
                self._write_json(500, {"error": type(exc).__name__, "message": str(exc)})

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve an ASSERT endpoint bridge for a RAMPART OpenClaw sandbox.")
    parser.add_argument("--sandbox-name", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--rampart-root", required=True)
    parser.add_argument("--port", type=int, default=18081)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--docker-command", default=os.environ.get("ASSERT_DOCKER_COMMAND", "docker.exe"))
    args = parser.parse_args()

    endpoint: OpenClawEndpoint | None = None
    last_error: Exception | None = None
    for attempt in range(1, 7):
        try:
            endpoint = OpenClawEndpoint(
                sandbox_name=args.sandbox_name,
                workspace=Path(args.workspace).expanduser().resolve(),
                rampart_root=Path(args.rampart_root).expanduser().resolve(),
                docker_command=args.docker_command,
            )
            break
        except Exception as exc:  # pragma: no cover - integration defensive retry
            last_error = exc
            if attempt == 6:
                raise
            print(f"OpenClaw endpoint bridge init failed on attempt {attempt}: {type(exc).__name__}: {exc}; retrying", flush=True)
            time.sleep(5)
    if endpoint is None:
        raise RuntimeError(f"OpenClaw endpoint bridge init failed: {last_error}")
    server = ThreadingHTTPServer((args.host, args.port), make_handler(endpoint))
    print(f"OpenClaw endpoint bridge ready on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
