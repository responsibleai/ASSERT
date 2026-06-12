# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from assert_ai.cli import cli
from assert_ai.local_sandbox import smoke_local_sandbox, start_local_sandbox, stop_local_sandbox


def _write_snapshot_manifest(base: Path, *, target: str = "openclaw") -> Path:
    snapshot_root = base / "snapshot"
    (snapshot_root / ".openclaw" / "workspace").mkdir(parents=True)
    (snapshot_root / ".openclaw" / "workspace" / "AGENTS.md").write_text("agent instructions\n", encoding="utf-8")
    (snapshot_root / "runtime" / "openclaw-package").mkdir(parents=True)
    (snapshot_root / "runtime" / "openclaw-package" / "package.json").write_text('{"name":"openclaw"}\n', encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "target": target,
        "agent": {"id": target, "display_name": target.title(), "kind": target, "status": "ready"},
        "snapshot_root": "snapshot",
        "copied_roots": [
            {"source": "[LOCAL_PATH]", "dest": ".openclaw", "files_copied": 1, "bytes_copied": 20},
            {"source": "[LOCAL_PATH]", "dest": "runtime/openclaw-package", "files_copied": 1, "bytes_copied": 20},
        ],
        "excluded_files": [],
        "safety": {
            "explicit_copy_roots_only": True,
            "secrets_excluded_by_path": True,
            "symlinks_not_copied": True,
        },
    }
    manifest_path = base / "snapshot_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path


def _server_command() -> str:
    code = """
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != '/health':
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps({'status': 'ok'}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get('Content-Length', '0'))
        self.rfile.read(length)
        body = json.dumps({'response': 'ok', 'events': [], 'metadata': {'runtime': 'fake'}}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass

server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
print(server.server_address[1], flush=True)
server.serve_forever()
"""
    return f"{sys.executable} -u -c {shlex.quote(code)}"


def test_start_local_sandbox_stages_snapshot_and_writes_state_and_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_AGENT_API_KEY", "super-secret-value")
    manifest_path = _write_snapshot_manifest(tmp_path / "input")
    result = start_local_sandbox(
        snapshot_manifest_path=manifest_path,
        target="openclaw",
        backend="command",
        command=_server_command(),
        endpoint_url="http://127.0.0.1:{port}",
        health_url="http://127.0.0.1:{port}/health",
        protocol="assert",
        model=None,
        api_key_env="LOCAL_AGENT_API_KEY",
        stream=False,
        output_dir=tmp_path / "sandbox-out",
        redact_paths=True,
    )

    assert result.process.poll() is None
    try:
        sandbox_root = tmp_path / "sandbox-out" / "sandbox"
        assert (sandbox_root / ".openclaw" / "workspace" / "AGENTS.md").read_text(encoding="utf-8") == "agent instructions\n"
        state_path = tmp_path / "sandbox-out" / "sandbox_state.json"
        config_path = tmp_path / "sandbox-out" / "endpoint_target.yaml"
        assert result.state_path == state_path
        assert result.config_path == config_path

        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state["schema_version"] == 1
        assert state["target"] == "openclaw"
        assert state["backend"] == "command"
        assert state["status"] == "running"
        assert state["endpoint"]["url"].startswith("http://127.0.0.1:")
        assert state["endpoint"]["protocol"] == "assert"
        assert state["endpoint"]["local_dev"] is True
        assert state["processes"][0]["pid"] == result.process.pid
        assert state["snapshot"]["manifest"] == "[LOCAL_PATH]"
        assert state["sandbox_root"] == "sandbox"
        assert "super-secret-value" not in state_path.read_text(encoding="utf-8")

        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        endpoint_config = config["pipeline"]["inference"]["target"]["endpoint"]
        assert endpoint_config == {
            "url": state["endpoint"]["url"],
            "protocol": "assert",
            "api_key_env": "LOCAL_AGENT_API_KEY",
            "stream": False,
            "local_dev": True,
        }
    finally:
        result.stop()


def test_smoke_local_sandbox_posts_message_and_returns_response(tmp_path: Path) -> None:
    manifest_path = _write_snapshot_manifest(tmp_path / "input")
    result = start_local_sandbox(
        snapshot_manifest_path=manifest_path,
        target="openclaw",
        backend="command",
        command=_server_command(),
        endpoint_url="http://127.0.0.1:{port}",
        health_url="http://127.0.0.1:{port}/health",
        protocol="assert",
        model=None,
        api_key_env=None,
        stream=False,
        output_dir=tmp_path / "sandbox-out",
        redact_paths=True,
    )

    try:
        smoke = smoke_local_sandbox(result.state_path, message="Reply exactly: ok", timeout_seconds=5)
    finally:
        result.stop()

    assert smoke["status"] == "ok"
    assert smoke["agent_endpoint"] == result.endpoint_url
    assert smoke["response"] == "ok"
    assert smoke["events"] == []
    assert smoke["metadata"] == {"runtime": "fake"}


def test_start_local_sandbox_requires_model_for_openai_chat(tmp_path: Path) -> None:
    manifest_path = _write_snapshot_manifest(tmp_path / "input")

    with pytest.raises(ValueError, match="model is required when protocol is openai_chat"):
        start_local_sandbox(
            snapshot_manifest_path=manifest_path,
            target="openclaw",
            backend="command",
            command=_server_command(),
            endpoint_url="http://127.0.0.1:{port}/v1/chat/completions",
            health_url="http://127.0.0.1:{port}/health",
            protocol="openai_chat",
            model=None,
            api_key_env=None,
            stream=False,
            output_dir=tmp_path / "sandbox-out",
            redact_paths=True,
        )


def test_cli_local_sandbox_smoke_posts_to_started_endpoint(tmp_path: Path) -> None:
    manifest_path = _write_snapshot_manifest(tmp_path / "input")
    start_result = start_local_sandbox(
        snapshot_manifest_path=manifest_path,
        target="openclaw",
        backend="command",
        command=_server_command(),
        endpoint_url="http://127.0.0.1:{port}",
        health_url="http://127.0.0.1:{port}/health",
        protocol="assert",
        model=None,
        api_key_env=None,
        stream=False,
        output_dir=tmp_path / "sandbox-out",
        redact_paths=True,
    )

    try:
        result = CliRunner().invoke(
            cli,
            [
                "local",
                "sandbox",
                "smoke",
                "--state",
                str(start_result.state_path),
                "--message",
                "Reply exactly: ok",
            ],
        )
    finally:
        start_result.stop()

    assert result.exit_code == 0, result.output
    assert "Sandbox smoke: ok" in result.output
    assert "response: ok" in result.output
    assert "events: 0" in result.output


def test_stop_local_sandbox_terminates_process_and_updates_state(tmp_path: Path) -> None:
    manifest_path = _write_snapshot_manifest(tmp_path / "input")
    result = start_local_sandbox(
        snapshot_manifest_path=manifest_path,
        target="openclaw",
        backend="command",
        command=_server_command(),
        endpoint_url="http://127.0.0.1:{port}",
        health_url="http://127.0.0.1:{port}/health",
        protocol="assert",
        model=None,
        api_key_env=None,
        stream=False,
        output_dir=tmp_path / "sandbox-out",
        redact_paths=True,
    )

    stop_result = stop_local_sandbox(result.state_path)

    assert stop_result["status"] == "stopped"
    assert result.process.poll() is not None
    state = json.loads(result.state_path.read_text(encoding="utf-8"))
    assert state["status"] == "stopped"
    assert state["stopped_at"]


def test_cli_local_sandbox_stop_terminates_started_endpoint(tmp_path: Path) -> None:
    manifest_path = _write_snapshot_manifest(tmp_path / "input")
    start_result = start_local_sandbox(
        snapshot_manifest_path=manifest_path,
        target="openclaw",
        backend="command",
        command=_server_command(),
        endpoint_url="http://127.0.0.1:{port}",
        health_url="http://127.0.0.1:{port}/health",
        protocol="assert",
        model=None,
        api_key_env=None,
        stream=False,
        output_dir=tmp_path / "sandbox-out",
        redact_paths=True,
    )

    result = CliRunner().invoke(
        cli,
        [
            "local",
            "sandbox",
            "stop",
            "--state",
            str(start_result.state_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Stopped local-agent sandbox" in result.output
    assert start_result.process.poll() is not None


def test_cli_local_sandbox_start_writes_state_and_config(tmp_path: Path) -> None:
    manifest_path = _write_snapshot_manifest(tmp_path / "input")
    out = tmp_path / "sandbox-out"

    result = CliRunner().invoke(
        cli,
        [
            "local",
            "sandbox",
            "start",
            "--snapshot",
            str(manifest_path),
            "--target",
            "openclaw",
            "--backend",
            "command",
            "--command",
            _server_command(),
            "--endpoint-url",
            "http://127.0.0.1:{port}",
            "--health-url",
            "http://127.0.0.1:{port}/health",
            "--output-dir",
            str(out),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Started local-agent sandbox" in result.output
    assert "endpoint: http://127.0.0.1:" in result.output
    assert "state:" in result.output
    assert "target config:" in result.output
    state = json.loads((out / "sandbox_state.json").read_text(encoding="utf-8"))
    assert state["status"] == "running"
    assert (out / "endpoint_target.yaml").exists()

    # Clean up the started fake server.
    import os
    import signal

    os.kill(state["processes"][0]["pid"], signal.SIGTERM)
