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
from assert_ai.local_sandbox import (
    DockerSandboxBackend,
    LocalSandboxLaunchContext,
    LocalSandboxLaunchPlan,
    LocalSandboxManagedProcess,
    LocalSandboxStep,
    OpenClawDockerLaunchDescriptor,
    RuntimeLaunchRecipe,
    build_descriptor_from_launch_recipe,
    smoke_local_sandbox,
    start_local_sandbox,
    start_openclaw_docker_sandbox,
    stop_local_sandbox,
)


def _write_snapshot_manifest(base: Path, *, target: str = "openclaw") -> Path:
    snapshot_root = base / "snapshot"
    (snapshot_root / ".openclaw" / "workspace").mkdir(parents=True)
    (snapshot_root / ".openclaw" / "workspace" / "AGENTS.md").write_text("agent instructions\n", encoding="utf-8")
    (snapshot_root / "runtime" / "openclaw-package").mkdir(parents=True)
    (snapshot_root / "runtime" / "openclaw-package" / "package.json").write_text('{"name":"openclaw"}\n', encoding="utf-8")
    (snapshot_root / "runtime" / "openclaw-package" / "openclaw.mjs").write_text("#!/usr/bin/env node\n", encoding="utf-8")
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


def _make_runner_root(base: Path) -> Path:
    runner_root = base / "runner"
    runner_root.mkdir()
    (runner_root / "start_openclaw_sandbox.ps1").write_text("# wrapper\n", encoding="utf-8")
    (runner_root / "mock_openai_server.py").write_text("# mock\n", encoding="utf-8")
    (runner_root / "openclaw_endpoint_bridge.py").write_text("# bridge\n", encoding="utf-8")
    return runner_root


def _server_command(response_text: str = "ok") -> str:
    code = f"""
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RESPONSE_TEXT = {response_text!r}

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != '/health':
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps({{'status': 'ok'}}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get('Content-Length', '0'))
        self.rfile.read(length)
        body = json.dumps({{'response': RESPONSE_TEXT, 'events': [], 'metadata': {{'runtime': 'fake', 'provider': 'copilot', 'model': 'gpt-5.5'}}}}).encode()
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
    assert smoke["metadata"] == {"runtime": "fake", "provider": "copilot", "model": "gpt-5.5"}


def test_smoke_local_sandbox_flags_openclaw_first_run_workspace_response(tmp_path: Path) -> None:
    manifest_path = _write_snapshot_manifest(tmp_path / "input")
    first_run_response = "Hey, I just came online in a fresh workspace. What should I call you?"
    result = start_local_sandbox(
        snapshot_manifest_path=manifest_path,
        target="openclaw",
        backend="command",
        command=_server_command(first_run_response),
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
        smoke = smoke_local_sandbox(result.state_path, configured_workspace_check=True, timeout_seconds=5)
    finally:
        result.stop()

    assert smoke["status"] == "failed"
    assert smoke["response"] == first_run_response
    assert smoke["configured_workspace_check"] == {
        "status": "failed",
        "failure_signals": ["came online", "fresh workspace", "what should i call you"],
    }


def test_cli_local_sandbox_smoke_prints_first_run_response_before_failing(tmp_path: Path) -> None:
    manifest_path = _write_snapshot_manifest(tmp_path / "input")
    first_run_response = "I came online in a fresh workspace, so what should I call you?"
    start_result = start_local_sandbox(
        snapshot_manifest_path=manifest_path,
        target="openclaw",
        backend="command",
        command=_server_command(first_run_response),
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
            ],
        )
    finally:
        start_result.stop()

    assert result.exit_code == 1
    assert first_run_response in result.output
    assert "Sandbox smoke: failed" in result.output
    assert "elapsed:" in result.output
    assert "configured workspace check failed" in result.output


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
    assert "provider: copilot" in result.output
    assert "model: gpt-5.5" in result.output
    assert "events: 0" in result.output
    assert "elapsed:" in result.output


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


def test_stop_local_sandbox_runs_docker_cleanup_commands(tmp_path: Path) -> None:
    state_path = tmp_path / "sandbox_state.json"
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "target": "openclaw",
                "backend": "docker",
                "status": "running",
                "endpoint": {"url": "http://127.0.0.1:18081"},
                "processes": [],
                "cleanup": {
                    "commands": [
                        {"name": "sandbox_stop", "command": ["python", "-c", "print('stop')"], "timeout_seconds": 5},
                        {"name": "sandbox_rm", "command": ["python", "-c", "print('rm')"], "timeout_seconds": 5},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

    result = stop_local_sandbox(state_path)

    assert result["status"] == "stopped"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["status"] == "stopped"
    assert [item["name"] for item in state["cleanup_results"]] == ["sandbox_stop", "sandbox_rm"]
    assert state["cleanup_results"][0]["exit_code"] == 0


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
    assert "elapsed:" in result.output
    assert start_result.process.poll() is not None


def test_docker_backend_for_openclaw_writes_product_state_without_public_rampart_label(tmp_path: Path) -> None:
    manifest_path = _write_snapshot_manifest(tmp_path / "input")
    rampart_root = tmp_path / "rampart-openclaw"
    (rampart_root / "scripts").mkdir(parents=True)
    (rampart_root / "scripts" / "openclaw-sandbox.ps1").write_text("# launcher\n", encoding="utf-8")
    (rampart_root / "scripts" / "run_auth_proxy.py").write_text("# auth proxy\n", encoding="utf-8")

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
            "docker",
            "--rampart-root",
            str(rampart_root),
            "--dry-run",
            "--output-dir",
            str(tmp_path / "sandbox-out"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Prepared local-agent sandbox" in result.output
    assert "backend: docker" in result.output
    assert "rampart" not in result.output.lower()
    assert "state:" in result.output
    assert "target config:" in result.output
    assert "elapsed:" in result.output

    state = json.loads((tmp_path / "sandbox-out" / "sandbox_state.json").read_text(encoding="utf-8"))
    state_text = (tmp_path / "sandbox-out" / "sandbox_state.json").read_text(encoding="utf-8")
    assert str(tmp_path) not in state_text
    assert state["target"] == "openclaw"
    assert state["backend"] == "docker"
    assert state["status"] == "planned"
    assert state["endpoint"] == {
        "url": "http://127.0.0.1:18081",
        "protocol": "assert",
        "stream": False,
        "local_dev": True,
    }
    assert state["plan"]["harness"] == "rampart-docker"
    assert [step["name"] for step in state["plan"]["steps"]] == [
        "preflight",
        "prepare_openclaw_runtime_archive",
        "start_mock_openai",
        "start_auth_proxy",
        "launch_rampart_sandbox",
        "start_endpoint_bridge",
    ]
    assert all(step["name"] != "run_behavior_suite" for step in state["plan"]["steps"])
    assert state["safety"]["live_home_mount"] is False
    config = yaml.safe_load((tmp_path / "sandbox-out" / "endpoint_target.yaml").read_text(encoding="utf-8"))
    assert config["pipeline"]["inference"]["target"]["endpoint"]["local_dev"] is True


def test_openclaw_docker_live_copilot_dry_run_generates_no_secret_auth_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_TOKEN", "gho_should_not_be_written")
    manifest_path = _write_snapshot_manifest(tmp_path / "input")
    runner_root = _make_runner_root(tmp_path)
    rampart_root = tmp_path / "rampart-openclaw"
    (rampart_root / "scripts").mkdir(parents=True)
    (rampart_root / "scripts" / "openclaw-sandbox.ps1").write_text("# launcher\n", encoding="utf-8")
    (rampart_root / "scripts" / "run_auth_proxy.py").write_text("# auth proxy\n", encoding="utf-8")

    result = start_openclaw_docker_sandbox(
        snapshot_manifest_path=manifest_path,
        target="openclaw",
        output_dir=tmp_path / "sandbox-out",
        runner_root=runner_root,
        rampart_root=rampart_root,
        sandbox_name="oc-live-test",
        provider="live",
        provider_route="copilot",
        model_ref="copilot/gpt-5.5=GPT 5.5 via Copilot",
        endpoint_port=19092,
        dry_run=True,
    )

    state_text = result.state_path.read_text(encoding="utf-8")
    state = json.loads(state_text)
    assert state["status"] == "planned"
    assert state["plan"]["provider"] == "live"
    assert state["plan"]["provider_route"] == "copilot"
    assert [step["name"] for step in state["plan"]["steps"]] == [
        "preflight",
        "prepare_openclaw_runtime_archive",
        "start_auth_proxy",
        "launch_rampart_sandbox",
        "start_endpoint_bridge",
    ]
    assert "start_mock_openai" not in {step["name"] for step in state["plan"]["steps"]}
    assert "gho_should_not_be_written" not in state_text

    auth_config_path = tmp_path / "sandbox-out" / "live-auth-proxy.json"
    auth_config_text = auth_config_path.read_text(encoding="utf-8")
    auth_config = json.loads(auth_config_text)
    assert auth_config == {
        "_comment": "No credential values are stored here. auth=copilot resolves a GitHub token host-side from env vars or gh CLI and injects it in the auth proxy.",
        "providers": {
            "copilot": {
                "enabled": True,
                "base_url": "https://api.githubcopilot.com",
                "auth": "copilot",
                "path_prefix": "/copilot",
            }
        },
    }
    assert "gho_should_not_be_written" not in auth_config_text


def test_cli_local_sandbox_start_live_copilot_does_not_require_manual_auth_config(tmp_path: Path) -> None:
    manifest_path = _write_snapshot_manifest(tmp_path / "input")
    rampart_root = tmp_path / "rampart-openclaw"
    (rampart_root / "scripts").mkdir(parents=True)
    (rampart_root / "scripts" / "openclaw-sandbox.ps1").write_text("# launcher\n", encoding="utf-8")
    (rampart_root / "scripts" / "run_auth_proxy.py").write_text("# auth proxy\n", encoding="utf-8")

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
            "docker",
            "--provider",
            "copilot",
            "--model",
            "gpt-5.5",
            "--rampart-root",
            str(rampart_root),
            "--dry-run",
            "--output-dir",
            str(tmp_path / "sandbox-out"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Prepared local-agent sandbox" in result.output
    assert "elapsed:" in result.output
    state = json.loads((tmp_path / "sandbox-out" / "sandbox_state.json").read_text(encoding="utf-8"))
    assert state["plan"]["provider"] == "live"
    assert state["plan"]["provider_route"] == "copilot"
    assert (tmp_path / "sandbox-out" / "live-auth-proxy.json").exists()



def test_cli_local_sandbox_start_help_is_product_shaped() -> None:
    result = CliRunner().invoke(cli, ["local", "sandbox", "start", "-h"])

    assert result.exit_code == 0, result.output
    assert "--provider [mock|copilot]" in result.output
    assert "live" not in result.output
    assert "--model-ref" not in result.output
    assert "--backend" not in result.output
    assert "--output-dir" not in result.output

def test_cli_local_sandbox_start_accepts_runtime_launch_recipe(tmp_path: Path) -> None:
    manifest_path = _write_fake_snapshot_manifest(tmp_path / "input", target="toy-agent")
    recipe_path = tmp_path / "toy-recipe.yaml"
    recipe_path.write_text(
        """
id: toy-agent
harness: rampart-docker
runtime_profile: profile
required_paths:
  - profile/AGENTS.md
launch_command:
  - python
  - -c
  - print('launch {sandbox_name} {endpoint_port}')
endpoint_port: 19000
sandbox_name: toy-sandbox
cleanup_labels:
  - toy-stop
""".lstrip(),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        cli,
        [
            "local",
            "sandbox",
            "start",
            "--snapshot",
            str(manifest_path),
            "--target",
            "toy-agent",
            "--recipe",
            str(recipe_path),
            "--dry-run",
            "--output-dir",
            str(tmp_path / "sandbox-out"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Prepared local-agent sandbox" in result.output
    assert "elapsed:" in result.output
    state = json.loads((tmp_path / "sandbox-out" / "sandbox_state.json").read_text(encoding="utf-8"))
    assert state["target"] == "toy-agent"
    assert state["plan"]["runner"] == "toy-agent"
    assert state["plan"]["runtime_profile"] == "profile"
    assert [step["name"] for step in state["plan"]["steps"]] == [
        "preflight",
        "start_mock_openai",
        "start_auth_proxy",
        "launch_rampart_sandbox",
    ]
    launch = next(step for step in state["plan"]["steps"] if step["name"] == "launch_rampart_sandbox")
    assert launch["command"] == ["python", "-c", "print('launch toy-sandbox 19000')"]
    assert "openclaw" not in (tmp_path / "sandbox-out" / "sandbox_state.json").read_text(encoding="utf-8").lower()



def test_cli_local_sandbox_start_happy_path_derives_live_copilot_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        cwd = Path.cwd()
        manifest_path = _write_snapshot_manifest(cwd / "input")
        rampart_root = cwd / "rampart-openclaw"
        (rampart_root / "scripts").mkdir(parents=True)
        (rampart_root / "scripts" / "openclaw-sandbox.ps1").write_text("# launcher\n", encoding="utf-8")
        (rampart_root / "scripts" / "run_auth_proxy.py").write_text("# auth proxy\n", encoding="utf-8")

        import assert_ai.local_sandbox as local_sandbox

        monkeypatch.setattr(local_sandbox, "_default_rampart_root", lambda: rampart_root)

        result = runner.invoke(
            cli,
            [
                "local",
                "sandbox",
                "start",
                "--snapshot",
                str(manifest_path),
                "--target",
                "openclaw",
                "--provider",
                "copilot",
                "--model",
                "gpt-5.5",
                "--dry-run",
            ],
        )

        assert result.exit_code == 0, result.output
        assert "Prepared local-agent sandbox" in result.output
        assert "backend: docker" in result.output
        assert "elapsed:" in result.output
        assert "model-ref" not in result.output
        assert "provider-route" not in result.output
        state_line = next(line for line in result.output.splitlines() if "state:" in line)
        state_path = Path(state_line.split("state:", 1)[1].strip())
        assert state_path.exists()
        assert "artifacts/local-agents/sandboxes/openclaw-" in state_path.as_posix()

        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state["backend"] == "docker"
        assert state["plan"]["provider"] == "live"
        assert state["plan"]["provider_route"] == "copilot"
        assert state["plan"]["model_ref"] == "copilot/gpt-5.5=GPT 5.5 via Copilot"
        assert state["cleanup"]["sandbox_name"].startswith("oc-")
        assert (state_path.parent / "live-auth-proxy.json").exists()


class FakeDockerLaunchDescriptor:
    target = "fake-agent"
    runner_name = "fake-runtime"

    def validate(self, context: LocalSandboxLaunchContext) -> None:
        assert (context.sandbox_root / "profile" / "AGENTS.md").exists()

    def build_plan(self, context: LocalSandboxLaunchContext) -> LocalSandboxLaunchPlan:
        return LocalSandboxLaunchPlan(
            steps=[
                LocalSandboxStep(
                    name="start_fake_runtime",
                    command=[sys.executable, "-c", "print('ready')"],
                    cwd=context.output_dir,
                    kind="service",
                    health_url=f"{context.endpoint_url}/health",
                    log_name="fake-runtime.log",
                )
            ],
            cleanup_commands=[],
            plan_metadata={"runtime_profile": "profile"},
        )


def _write_fake_snapshot_manifest(base: Path, *, target: str = "fake-agent") -> Path:
    snapshot_root = base / "snapshot"
    (snapshot_root / "profile").mkdir(parents=True)
    (snapshot_root / "profile" / "AGENTS.md").write_text("fake runtime instructions\n", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "target": target,
        "agent": {"id": target, "display_name": "Fake Agent", "kind": "fake", "status": "ready"},
        "snapshot_root": "snapshot",
        "copied_roots": [{"source": "[LOCAL_PATH]", "dest": "profile", "files_copied": 1, "bytes_copied": 20}],
        "excluded_files": [],
        "safety": {"explicit_copy_roots_only": True},
    }
    manifest_path = base / "snapshot_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path


def test_docker_backend_runs_runtime_descriptor_without_openclaw_assumptions(tmp_path: Path) -> None:
    manifest_path = _write_fake_snapshot_manifest(tmp_path / "input")
    executed: list[str] = []

    def start_step(step: LocalSandboxStep, log_path: Path) -> LocalSandboxManagedProcess:
        executed.append(f"service:{step.name}:{step.cwd.name}")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("ready\n", encoding="utf-8")
        return LocalSandboxManagedProcess(name=step.name, pid=3100, log_path=log_path)

    backend = DockerSandboxBackend(descriptor=FakeDockerLaunchDescriptor())

    result = backend.start(
        snapshot_manifest_path=manifest_path,
        target="fake-agent",
        output_dir=tmp_path / "sandbox-out",
        endpoint_url="http://127.0.0.1:19999",
        provider="mock",
        protocol="assert",
        model=None,
        api_key_env=None,
        stream=False,
        redact_paths=True,
        dry_run=False,
        start_step=start_step,
    )

    assert executed == ["service:start_fake_runtime:sandbox-out"]
    state = json.loads(result.state_path.read_text(encoding="utf-8"))
    assert state["target"] == "fake-agent"
    assert state["backend"] == "docker"
    assert state["status"] == "running"
    assert state["plan"]["runner"] == "fake-runtime"
    assert state["plan"]["runtime_profile"] == "profile"
    assert [step["name"] for step in state["plan"]["steps"]] == ["start_fake_runtime"]
    assert "openclaw" not in result.state_path.read_text(encoding="utf-8").lower()


def test_rampart_harness_builds_reusable_launch_steps_without_openclaw_assumptions(tmp_path: Path) -> None:
    from assert_ai.local_sandbox import RampartDockerHarness, RampartRuntimeDescriptor

    descriptor = RampartRuntimeDescriptor(
        runner_id="toy-agent",
        runtime_profile="toy-profile",
        required_paths=("toy/home",),
        endpoint_bridge_module="assert_ai.local_sandbox_helpers.toy_endpoint_bridge",
        endpoint_bridge_args=("--state", "toy"),
        launch_command=("python", "-m", "toy.launch", "--sandbox", "toy-sandbox"),
        cleanup_labels=("toy-stop",),
    )
    context = LocalSandboxLaunchContext(
        manifest_path=tmp_path / "snapshot_manifest.json",
        manifest={"target": "toy-agent"},
        output_dir=tmp_path / "run",
        sandbox_root=tmp_path / "run" / "sandbox",
        logs_dir=tmp_path / "run" / "logs",
        endpoint_url="http://127.0.0.1:18081",
        endpoint_port=18081,
        endpoint={"url": "http://127.0.0.1:18081", "protocol": "assert", "local_dev": True},
        provider="mock",
        provider_route=None,
        model_ref="openai/mock-model=Mock Model",
        auth_proxy_port=12435,
        mock_openai_port=18080,
        auth_proxy_config=None,
        runner_root=tmp_path / "runner",
        rampart_root=tmp_path / "rampart",
        docker_command="docker.exe",
        sandbox_name="toy-sandbox",
        docker_timeout_seconds=60,
        dry_run=True,
    )

    plan = RampartDockerHarness(descriptor=descriptor).build_plan(context)

    assert plan.runner == "toy-agent"
    assert plan.runtime_profile == "toy-profile"
    assert [step.name for step in plan.steps] == [
        "preflight",
        "start_mock_openai",
        "start_auth_proxy",
        "launch_rampart_sandbox",
        "start_endpoint_bridge",
    ]
    launch_step = next(step for step in plan.steps if step.name == "launch_rampart_sandbox")
    assert launch_step.command == ["python", "-m", "toy.launch", "--sandbox", "toy-sandbox"]
    assert all("openclaw" not in " ".join(step.command).lower() for step in plan.steps)
    assert plan.cleanup_commands
    assert plan.cleanup_commands[0].name == "toy-stop"


def test_runtime_launch_recipe_builds_rampart_descriptor_without_runtime_class() -> None:
    recipe = RuntimeLaunchRecipe(
        id="toy-agent",
        harness="rampart-docker",
        runtime_profile="toy-profile",
        required_paths=("toy/home",),
        launch_command=("python", "-m", "toy.launch", "--home", "{sandbox_root}/toy/home", "--port", "{endpoint_port}"),
        endpoint_bridge_module="assert_ai.local_sandbox_helpers.toy_endpoint_bridge",
        endpoint_bridge_args=("--sandbox-root", "{sandbox_root}", "--endpoint-port", "{endpoint_port}"),
        cleanup_labels=("toy-stop",),
    )

    descriptor = build_descriptor_from_launch_recipe(recipe)

    assert descriptor.runner_id == "toy-agent"
    assert descriptor.runtime_profile == "toy-profile"
    assert descriptor.required_paths == ("toy/home",)
    assert descriptor.launch_command == (
        "python",
        "-m",
        "toy.launch",
        "--home",
        "{sandbox_root}/toy/home",
        "--port",
        "{endpoint_port}",
    )
    assert descriptor.endpoint_bridge_args == ("--sandbox-root", "{sandbox_root}", "--endpoint-port", "{endpoint_port}")


def test_docker_backend_runs_launch_recipe_descriptor_without_openclaw_assumptions(tmp_path: Path) -> None:
    manifest_path = _write_fake_snapshot_manifest(tmp_path / "input", target="toy-agent")
    recipe = RuntimeLaunchRecipe(
        id="toy-agent",
        harness="rampart-docker",
        runtime_profile="profile",
        required_paths=("profile/AGENTS.md",),
        launch_command=(sys.executable, "-c", "print('launch {sandbox_name} {endpoint_port}')"),
        endpoint_bridge_module=None,
        cleanup_labels=("toy-stop",),
        endpoint_port=19000,
        sandbox_name="toy-sandbox",
    )
    executed: list[str] = []

    def run_step(step: LocalSandboxStep, log_path: Path) -> int:
        executed.append(f"run:{step.name}:{' '.join(step.command)}")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("ok\n", encoding="utf-8")
        return 0

    def start_step(step: LocalSandboxStep, log_path: Path) -> LocalSandboxManagedProcess:
        executed.append(f"service:{step.name}")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("ready\n", encoding="utf-8")
        return LocalSandboxManagedProcess(name=step.name, pid=4100 + len(executed), log_path=log_path)

    backend = DockerSandboxBackend(descriptor=build_descriptor_from_launch_recipe(recipe))
    result = backend.start(
        snapshot_manifest_path=manifest_path,
        target="toy-agent",
        output_dir=tmp_path / "sandbox-out",
        endpoint_url="http://127.0.0.1:19000",
        provider="mock",
        protocol="assert",
        model=None,
        api_key_env=None,
        stream=False,
        redact_paths=True,
        dry_run=False,
        run_step=run_step,
        start_step=start_step,
    )

    state = json.loads(result.state_path.read_text(encoding="utf-8"))
    assert state["target"] == "toy-agent"
    assert state["plan"]["runner"] == "toy-agent"
    assert state["plan"]["runtime_profile"] == "profile"
    assert [step["name"] for step in state["plan"]["steps"]] == [
        "preflight",
        "start_mock_openai",
        "start_auth_proxy",
        "launch_rampart_sandbox",
    ]
    assert any("launch toy-sandbox 19000" in item for item in executed)
    assert "openclaw" not in result.state_path.read_text(encoding="utf-8").lower()


def test_openclaw_runtime_descriptor_exposes_docker_launch_plan(tmp_path: Path) -> None:
    manifest_path = _write_snapshot_manifest(tmp_path / "input")
    runner_root = _make_runner_root(tmp_path)
    rampart_root = tmp_path / "rampart-openclaw"
    (rampart_root / "scripts").mkdir(parents=True)
    (rampart_root / "scripts" / "openclaw-sandbox.ps1").write_text("# launcher\n", encoding="utf-8")
    (rampart_root / "scripts" / "run_auth_proxy.py").write_text("# auth proxy\n", encoding="utf-8")

    backend = DockerSandboxBackend(
        descriptor=OpenClawDockerLaunchDescriptor(
            runner_root=runner_root,
            rampart_root=rampart_root,
            sandbox_name="oc-descriptor-test",
            provider="mock",
            model_ref="openai/mock-model=Mock Model",
            endpoint_port=19998,
            auth_proxy_port=19997,
            mock_openai_port=19996,
            docker_command="docker.exe",
        )
    )
    result = backend.start(
        snapshot_manifest_path=manifest_path,
        target="openclaw",
        output_dir=tmp_path / "sandbox-out",
        endpoint_url="http://127.0.0.1:19998",
        provider="mock",
        protocol="assert",
        model=None,
        api_key_env=None,
        stream=False,
        redact_paths=True,
        dry_run=True,
    )

    state = json.loads(result.state_path.read_text(encoding="utf-8"))
    assert state["plan"]["runner"] == "openclaw-docker-sandbox"
    assert state["plan"]["workspace_fidelity"] == {
        "active_workspace": "/home/agent/.openclaw/workspace",
        "verified_by": "endpoint_bridge_sentinel_hashes",
    }
    assert [step["name"] for step in state["plan"]["steps"]] == [
        "preflight",
        "prepare_openclaw_runtime_archive",
        "start_mock_openai",
        "start_auth_proxy",
        "launch_rampart_sandbox",
        "start_endpoint_bridge",
    ]


def test_openclaw_docker_backend_executes_setup_steps_and_writes_running_state(tmp_path: Path) -> None:
    manifest_path = _write_snapshot_manifest(tmp_path / "input")
    runner_root = _make_runner_root(tmp_path)
    rampart_root = tmp_path / "rampart-openclaw"
    (rampart_root / "scripts").mkdir(parents=True)
    (rampart_root / "scripts" / "openclaw-sandbox.ps1").write_text("# launcher\n", encoding="utf-8")
    (rampart_root / "scripts" / "run_auth_proxy.py").write_text("# auth proxy\n", encoding="utf-8")
    executed: list[str] = []
    cleanup_commands: list[list[str]] = []

    class FakeProcess:
        def __init__(self, pid: int) -> None:
            self.pid = pid
            self.returncode = None

        def poll(self) -> int | None:
            return self.returncode

    def run_step(step: LocalSandboxStep, log_path: Path) -> int:
        executed.append(f"run:{step.name}")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("ok\n", encoding="utf-8")
        return 0

    def start_step(step: LocalSandboxStep, log_path: Path) -> LocalSandboxManagedProcess:
        executed.append(f"service:{step.name}")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("ready\n", encoding="utf-8")
        return LocalSandboxManagedProcess(name=step.name, pid=1000 + len(executed), log_path=log_path)

    def cleanup(command: list[str], log_path: Path, timeout_seconds: int) -> int:
        cleanup_commands.append(command)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("clean\n", encoding="utf-8")
        return 0

    result = start_openclaw_docker_sandbox(
        snapshot_manifest_path=manifest_path,
        target="openclaw",
        output_dir=tmp_path / "sandbox-out",
        runner_root=runner_root,
        rampart_root=rampart_root,
        sandbox_name="oc-test",
        provider="mock",
        model_ref="openai/mock-model=Mock Model",
        endpoint_port=19091,
        protocol="assert",
        dry_run=False,
        run_step=run_step,
        start_step=start_step,
        cleanup_step=cleanup,
    )

    assert executed == [
        "run:preflight",
        "run:prepare_openclaw_runtime_archive",
        "service:start_mock_openai",
        "service:start_auth_proxy",
        "run:launch_rampart_sandbox",
        "service:start_endpoint_bridge",
    ]
    assert cleanup_commands == []
    state = json.loads(result.state_path.read_text(encoding="utf-8"))
    assert state["status"] == "running"
    assert state["backend"] == "docker"
    assert state["endpoint"]["url"] == "http://127.0.0.1:19091"
    assert [process["name"] for process in state["processes"]] == [
        "start_mock_openai",
        "start_auth_proxy",
        "start_endpoint_bridge",
    ]
    assert [command["name"] for command in state["cleanup"]["commands"]] == ["sandbox_stop", "sandbox_rm"]
    assert state["plan"]["steps"][-1]["name"] == "start_endpoint_bridge"
    assert state["plan"]["steps"][-1]["health_url"] == "http://127.0.0.1:19091/health"
    assert "run_behavior_suite" not in {step["name"] for step in state["plan"]["steps"]}


def test_openclaw_docker_backend_cleans_started_services_when_later_step_fails(tmp_path: Path) -> None:
    manifest_path = _write_snapshot_manifest(tmp_path / "input")
    runner_root = _make_runner_root(tmp_path)
    rampart_root = tmp_path / "rampart-openclaw"
    (rampart_root / "scripts").mkdir(parents=True)
    (rampart_root / "scripts" / "openclaw-sandbox.ps1").write_text("# launcher\n", encoding="utf-8")
    (rampart_root / "scripts" / "run_auth_proxy.py").write_text("# auth proxy\n", encoding="utf-8")
    events: list[str] = []

    def run_step(step: LocalSandboxStep, log_path: Path) -> int:
        events.append(f"run:{step.name}")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("boom\n" if step.name == "launch_rampart_sandbox" else "ok\n", encoding="utf-8")
        return 7 if step.name == "launch_rampart_sandbox" else 0

    def start_step(step: LocalSandboxStep, log_path: Path) -> LocalSandboxManagedProcess:
        events.append(f"service:{step.name}")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("ready\n", encoding="utf-8")
        return LocalSandboxManagedProcess(name=step.name, pid=2000 + len(events), log_path=log_path)

    def stop_process(pid: int, timeout_seconds: float) -> str:
        events.append(f"stop:{pid}")
        return "stopped"

    def cleanup(command: list[str], log_path: Path, timeout_seconds: int) -> int:
        events.append(f"cleanup:{command[-1]}")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("clean\n", encoding="utf-8")
        return 0

    with pytest.raises(ValueError, match="launch_rampart_sandbox failed with exit code 7"):
        start_openclaw_docker_sandbox(
            snapshot_manifest_path=manifest_path,
            target="openclaw",
            output_dir=tmp_path / "sandbox-out",
            runner_root=runner_root,
            rampart_root=rampart_root,
            sandbox_name="oc-test",
            provider="mock",
            dry_run=False,
            run_step=run_step,
            start_step=start_step,
            stop_process=stop_process,
            cleanup_step=cleanup,
        )

    assert "stop:2004" in events
    assert "stop:2003" in events
    assert events.index("stop:2004") < events.index("stop:2003")
    assert "cleanup:oc-test" in events


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
