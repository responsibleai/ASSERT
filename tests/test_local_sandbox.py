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
from assert_ai.local_agent_config import AgentRuntimeConfig, ConfigRoot, EndpointSpec, LaunchSpec
from assert_ai.local_sandbox import (
    DockerSandboxBackend,
    LocalSandboxLaunchContext,
    LocalSandboxLaunchPlan,
    LocalSandboxManagedProcess,
    LocalSandboxStep,
    OpenClawDockerLaunchDescriptor,
    RuntimeLaunchConfig,
    RuntimeModelRouting,
    build_descriptor_from_runtime_config,
    build_runtime_config_from_agent_config,
    smoke_local_sandbox,
    start_local_sandbox,
    start_openclaw_docker_sandbox,
    stop_local_sandbox,
)


def _write_snapshot_manifest(base: Path, *, target: str = "openclaw", source: str | None = None) -> Path:
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
    if source is not None:
        manifest["source"] = source
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



def test_model_routing_path_setter_supports_array_indexes_and_literal_colon_keys() -> None:
    from assert_ai.local_sandbox import _get_config_path, _set_config_path

    payload: dict[str, object] = {}

    _set_config_path(payload, "auth.profiles.github-copilot:github.provider", "copilot")
    _set_config_path(payload, "models.providers.github-copilot.models[0].api", "codex_responses")

    assert payload["auth"]["profiles"]["github-copilot:github"]["provider"] == "copilot"
    assert payload["models"]["providers"]["github-copilot"]["models"][0]["api"] == "codex_responses"
    assert _get_config_path(payload, "auth.profiles.github-copilot:github.provider") == "copilot"
    assert _get_config_path(payload, "models.providers.github-copilot.models[0].api") == "codex_responses"



def test_model_routing_path_supports_numeric_segments_as_list_indexes() -> None:
    from assert_ai.local_sandbox import _get_config_path, _set_config_path

    payload: dict[str, object] = {}
    _set_config_path(payload, "models.providers.github-copilot.models.0.api", "openai-responses")

    assert payload["models"]["providers"]["github-copilot"]["models"][0]["api"] == "openai-responses"
    assert _get_config_path(payload, "models.providers.github-copilot.models.0.api") == "openai-responses"

def test_model_routing_rewrite_materializes_missing_required_config(tmp_path: Path) -> None:
    from assert_ai.local_sandbox import RuntimeModelRouting, _rewrite_model_routing_config

    routing = RuntimeModelRouting(
        staged_config_file=".openclaw/openclaw.json",
        provider_key="auth.profiles.github-copilot:github.provider",
        model_key="agents.defaults.model.primary",
        api_mode_key="models.providers.github-copilot.models[0].api",
        base_url_key="models.providers.github-copilot.baseUrl",
        api_key_key="models.providers.github-copilot.apiKey",
        resolved_provider="copilot",
        resolved_api_mode="codex_responses",
        resolved_model="github-copilot/gpt-5.5",
        create_if_missing=True,
        format="json",
    )

    _rewrite_model_routing_config(tmp_path, routing, auth_proxy_port=12435, provider_route="copilot", endpoint_api_key="endpoint-token")

    materialized = tmp_path / ".openclaw" / "openclaw.json"
    assert materialized.exists()
    payload = json.loads(materialized.read_text(encoding="utf-8"))
    assert payload["auth"]["profiles"]["github-copilot:github"]["provider"] == "copilot"
    provider_config = payload["models"]["providers"]["assert-local-proxy"]
    assert isinstance(provider_config, dict)
    assert provider_config["baseUrl"] == "http://host.docker.internal:12435/copilot"
    assert payload["models"]["mode"] == "replace"
    assert provider_config["apiKey"] == "proxy-managed"
    assert provider_config["auth"] == "api-key"
    assert provider_config["request"]["allowPrivateNetwork"] is True
    model_entry = provider_config["models"][0]
    assert model_entry["api"] == "codex_responses"
    assert model_entry["id"] == "gpt-5.5"
    assert model_entry["name"] == "gpt-5.5"
    assert payload["agents"]["defaults"]["model"]["primary"] == "assert-local-proxy/gpt-5.5"
    assert payload["gateway"]["mode"] == "local"
    assert payload["gateway"]["http"]["endpoints"]["chatCompletions"]["enabled"] is True
    assert payload["gateway"]["auth"]["mode"] == "token"
    assert payload["gateway"]["auth"]["token"] == "endpoint-token"


def test_model_routing_rewrite_refreshes_endpoint_token_for_existing_materialized_config(tmp_path: Path) -> None:
    from assert_ai.local_sandbox import RuntimeModelRouting, _rewrite_model_routing_config

    config_path = tmp_path / ".openclaw" / "openclaw.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({"gateway": {"mode": "local", "auth": {"mode": "token", "token": "old-token"}}}),
        encoding="utf-8",
    )
    routing = RuntimeModelRouting(
        staged_config_file=".openclaw/openclaw.json",
        base_url_key="models.providers.github-copilot.baseUrl",
        api_key_key="models.providers.github-copilot.apiKey",
        create_if_missing=True,
        format="json",
    )

    _rewrite_model_routing_config(tmp_path, routing, auth_proxy_port=12435, provider_route="copilot", endpoint_api_key="new-token")

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["gateway"]["auth"]["mode"] == "token"
    assert payload["gateway"]["auth"]["token"] == "new-token"

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


def _write_sandbox_state(
    sandboxes_dir: Path,
    name: str,
    *,
    status: str,
    pids: list[int],
) -> Path:
    run_dir = sandboxes_dir / name
    run_dir.mkdir(parents=True)
    state_path = run_dir / "sandbox_state.json"
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "target": "openclaw",
                "backend": "docker",
                "status": status,
                "endpoint": {"url": "http://127.0.0.1:18081"},
                "processes": [{"name": "endpoint", "pid": pid} for pid in pids],
            }
        ),
        encoding="utf-8",
    )
    return state_path


def test_find_running_sandbox_state_returns_single_live_sandbox(tmp_path: Path) -> None:
    from assert_ai.local_sandbox import find_running_sandbox_state

    sandboxes = tmp_path / "artifacts" / "local-agents" / "sandboxes"
    # A live process: the current interpreter is guaranteed alive.
    import os as _os

    live = _write_sandbox_state(sandboxes, "oc-live", status="running", pids=[_os.getpid()])
    # A stale state: status says running but the pid is long dead.
    _write_sandbox_state(sandboxes, "oc-stale", status="running", pids=[2_000_000_000])
    # A stopped state should be ignored.
    _write_sandbox_state(sandboxes, "oc-stopped", status="stopped", pids=[_os.getpid()])

    found = find_running_sandbox_state(sandboxes_dir=sandboxes)

    assert found == live


def test_find_running_sandbox_state_errors_when_none_running(tmp_path: Path) -> None:
    from assert_ai.local_sandbox import find_running_sandbox_state

    sandboxes = tmp_path / "artifacts" / "local-agents" / "sandboxes"
    _write_sandbox_state(sandboxes, "oc-stale", status="running", pids=[2_000_000_000])

    with pytest.raises(ValueError, match="no running local-agent sandbox"):
        find_running_sandbox_state(sandboxes_dir=sandboxes)


def test_find_running_sandbox_state_errors_when_multiple_running(tmp_path: Path) -> None:
    from assert_ai.local_sandbox import find_running_sandbox_state

    import os as _os

    sandboxes = tmp_path / "artifacts" / "local-agents" / "sandboxes"
    _write_sandbox_state(sandboxes, "oc-a", status="running", pids=[_os.getpid()])
    _write_sandbox_state(sandboxes, "oc-b", status="running", pids=[_os.getpid()])

    with pytest.raises(ValueError, match="multiple running local-agent sandboxes"):
        find_running_sandbox_state(sandboxes_dir=sandboxes)


def test_find_staged_openclaw_runtime_accepts_basename_dest(tmp_path: Path) -> None:
    """A self-introspected config with no explicit dest derives the runtime package
    to its basename ('openclaw/'). The descriptor must locate it by content
    (package.json + openclaw.mjs), not only by the canonical 'runtime/openclaw-package'.
    """
    from assert_ai.local_sandbox import _find_staged_openclaw_runtime

    sandbox_root = tmp_path / "snapshot"
    pkg = sandbox_root / "openclaw"
    pkg.mkdir(parents=True)
    (pkg / "package.json").write_text('{"name":"openclaw"}\n', encoding="utf-8")
    (pkg / "openclaw.mjs").write_text("#!/usr/bin/env node\n", encoding="utf-8")

    found = _find_staged_openclaw_runtime(sandbox_root)

    assert found == pkg


def test_find_staged_workspace_accepts_dotopenclaw_workspace(tmp_path: Path) -> None:
    """A self-introspected config copies ~/.openclaw to dest '.openclaw', so the
    workspace lands at '.openclaw/workspace' — which the descriptor must accept.
    """
    from assert_ai.local_sandbox import _find_staged_workspace

    sandbox_root = tmp_path / "snapshot"
    ws = sandbox_root / ".openclaw" / "workspace"
    ws.mkdir(parents=True)
    (ws / "AGENTS.md").write_text("# workspace\n", encoding="utf-8")

    found = _find_staged_workspace(sandbox_root)

    assert found == ws


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


def test_cli_local_sandbox_stop_infers_single_running_sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import assert_ai.local_sandbox as local_sandbox

    sandboxes_dir = tmp_path / "artifacts" / "local-agents" / "sandboxes"
    monkeypatch.setattr(local_sandbox, "DEFAULT_SANDBOXES_DIR", sandboxes_dir)

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
        output_dir=sandboxes_dir / "oc-run",
        redact_paths=True,
    )

    # No --state: the command must infer the single running sandbox.
    result = CliRunner().invoke(cli, ["local", "sandbox", "stop"])

    assert result.exit_code == 0, result.output
    assert "Stopped local-agent sandbox" in result.output
    assert start_result.process.poll() is not None


def test_cli_local_sandbox_stop_errors_with_no_running_sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import assert_ai.local_sandbox as local_sandbox

    sandboxes_dir = tmp_path / "artifacts" / "local-agents" / "sandboxes"
    monkeypatch.setattr(local_sandbox, "DEFAULT_SANDBOXES_DIR", sandboxes_dir)

    result = CliRunner().invoke(cli, ["local", "sandbox", "stop"])

    assert result.exit_code != 0
    assert "no running local-agent sandbox" in result.output


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


def test_docker_backend_accepts_agent_config_manifest_with_free_form_target(tmp_path: Path) -> None:
    """A self-introspected agent config declares a free-form `id` (e.g.
    'openclaw-main-jp-desktop') which becomes the manifest `target`. When the
    manifest was produced by an agent config (source == 'agent_config'), the
    free-form target must not block selecting the OpenClaw descriptor via
    `--target openclaw`. Downstream content-validation is the real guard.
    """
    manifest_path = _write_snapshot_manifest(
        tmp_path / "input", target="openclaw-main-jp-desktop", source="agent_config"
    )
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
    state = json.loads((tmp_path / "sandbox-out" / "sandbox_state.json").read_text(encoding="utf-8"))
    assert state["target"] == "openclaw"
    assert state["status"] == "planned"


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

def test_cli_local_sandbox_start_accepts_runtime_config(tmp_path: Path) -> None:
    manifest_path = _write_fake_snapshot_manifest(tmp_path / "input", target="toy-agent")
    runtime_config_path = tmp_path / "toy-runtime-config.yaml"
    runtime_config_path.write_text(
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
            "--runtime-config",
            str(runtime_config_path),
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


def test_cli_local_sandbox_start_drives_generic_run_from_agent_config(tmp_path: Path) -> None:
    # The payoff: a single self-introspected agent-config drives the generic run.
    # No --runtime-config, no --target -- everything is derived from the one file.
    manifest_path = _write_fake_snapshot_manifest(tmp_path / "input", target="toy-agent")
    rampart_root = tmp_path / "rampart-scripts"
    (rampart_root / "scripts").mkdir(parents=True)
    (rampart_root / "scripts" / "run_auth_proxy.py").write_text("# proxy\n", encoding="utf-8")
    agent_config_path = tmp_path / "agent.yaml"
    agent_config_path.write_text(
        """
id: toy-agent
display_name: Toy Agent
roots:
  - source: ~/.toy
launch:
  command:
    - python
    - -c
    - print('launch {sandbox_name} {endpoint_port}')
endpoint:
  url: http://127.0.0.1:19000/v1/chat/completions
  protocol: openai_chat
  model: toy-model
model_routing:
  resolved_provider: copilot
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
            "--config",
            str(agent_config_path),
            "--rampart-root",
            str(rampart_root),
            "--dry-run",
            "--output-dir",
            str(tmp_path / "sandbox-out"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Prepared local-agent sandbox" in result.output
    state = json.loads((tmp_path / "sandbox-out" / "sandbox_state.json").read_text(encoding="utf-8"))
    # target derived from the agent-config id
    assert state["target"] == "toy-agent"
    # endpoint protocol/model derived from the agent-config endpoint
    assert state["endpoint"]["protocol"] == "openai_chat"
    assert state["endpoint"]["model"] == "toy-model"
    # The agent's launch.command is the runtime command to run inside the clone.
    # It must not be executed as the host-side sandbox launcher.
    launch = next(step for step in state["plan"]["steps"] if step["name"] == "launch_rampart_sandbox")
    assert "print('launch {sandbox_name} {endpoint_port}')" not in " ".join(launch["command"])
    assert "-RuntimeCommandFile" in launch["command"]
    assert "-IdentityStagingFile" in launch["command"]
    assert state["plan"]["runtime_command"] == ["python", "-c", "print('launch {sandbox_name} {endpoint_port}')"]


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


def test_harness_module_references_are_importable() -> None:
    """The harness spawns helper modules by dotted path; they must resolve.

    Regression: the mock-server and openclaw-bridge references used a stale
    'local_sandbox_helpers' package name that never existed, so any --provider
    mock run died at start_mock_openai with ModuleNotFoundError. Live (copilot)
    runs skipped the mock step and never surfaced it.
    """
    import importlib.util

    referenced_modules = [
        "assert_ai.local_sandbox_runtime.mock_openai_server",
        "assert_ai.local_sandbox_runtime.openclaw_endpoint_bridge",
    ]
    for module_name in referenced_modules:
        assert importlib.util.find_spec(module_name) is not None, f"harness references missing module: {module_name}"

    # And the strings actually used in the harness/descriptor must point at those.
    source = (Path(__file__).resolve().parent.parent / "assert_ai" / "local_sandbox.py").read_text(encoding="utf-8")
    assert "local_sandbox_helpers" not in source, "stale local_sandbox_helpers reference in local_sandbox.py"


def test_runtime_config_builds_rampart_descriptor_without_runtime_class() -> None:
    runtime_config = RuntimeLaunchConfig(
        id="toy-agent",
        harness="rampart-docker",
        runtime_profile="toy-profile",
        required_paths=("toy/home",),
        launch_command=("python", "-m", "toy.launch", "--home", "{sandbox_root}/toy/home", "--port", "{endpoint_port}"),
        endpoint_bridge_module="assert_ai.local_sandbox_helpers.toy_endpoint_bridge",
        endpoint_bridge_args=("--sandbox-root", "{sandbox_root}", "--endpoint-port", "{endpoint_port}"),
        cleanup_labels=("toy-stop",),
    )

    descriptor = build_descriptor_from_runtime_config(runtime_config)

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


def test_agent_config_bridge_builds_private_identity_staging_plan(tmp_path: Path) -> None:
    hermes_home = tmp_path / "home" / "jake" / ".hermes"
    local_ops = tmp_path / "home" / "jake" / "LocalOps" / "hermes"
    hermes_home.mkdir(parents=True)
    local_ops.mkdir(parents=True)
    config = AgentRuntimeConfig(
        id="hermes-default",
        roots=[ConfigRoot(source=hermes_home)],
        external_dependencies=[ConfigRoot(source=local_ops, kind="external_dependency")],
        launch=LaunchSpec(command=("python", "-m", "hermes_cli.main", "gateway", "run")),
        endpoint=EndpointSpec(url="http://127.0.0.1:8642/v1/chat/completions", protocol="openai_chat", model="gpt-5.5"),
    )

    runtime_config = build_runtime_config_from_agent_config(config)

    assert runtime_config.launch_command is not None
    launch_text = " ".join(runtime_config.launch_command)
    assert "start_openclaw_sandbox.ps1" in launch_text
    assert "-SnapshotRoot" in runtime_config.launch_command
    assert "{sandbox_root}" in runtime_config.launch_command
    assert "-RuntimeCommandFile" in runtime_config.launch_command
    assert "{runtime_command_file}" in runtime_config.launch_command
    assert "-IdentityStagingFile" in runtime_config.launch_command
    assert "{identity_staging_file}" in runtime_config.launch_command
    assert "-EndpointPort" in runtime_config.launch_command
    assert "{endpoint_port}" in runtime_config.launch_command
    assert runtime_config.identity_staging == (
        {"snapshot_path": ".hermes", "container_path": str(hermes_home.resolve())},
        {"snapshot_path": "hermes", "container_path": str(local_ops.resolve())},
    )


def test_identity_staging_metadata_is_redacted_in_dry_run_state(tmp_path: Path) -> None:
    manifest_path = _write_fake_snapshot_manifest(tmp_path / "input", target="toy-agent")
    runtime_config = RuntimeLaunchConfig(
        id="toy-agent",
        harness="rampart-docker",
        runtime_profile="profile",
        required_paths=("profile/AGENTS.md",),
        runtime_command=("python", "-m", "toy"),
        identity_staging=(
            {"snapshot_path": "profile", "container_path": str(tmp_path / "home" / "jake" / ".toy")},
        ),
    )

    backend = DockerSandboxBackend(descriptor=build_descriptor_from_runtime_config(runtime_config))
    result = backend.start(
        snapshot_manifest_path=manifest_path,
        target="toy-agent",
        output_dir=tmp_path / "sandbox-out",
        endpoint_url="http://127.0.0.1:19000",
        provider="mock",
        protocol="assert",
        dry_run=True,
        redact_paths=True,
    )

    state = json.loads(result.state_path.read_text(encoding="utf-8"))
    assert state["plan"]["identity_staging"] == [
        {"snapshot_path": "profile", "container_path": "[LOCAL_PATH]"},
    ]
    assert str(tmp_path) not in result.state_path.read_text(encoding="utf-8")


def test_runtime_config_carries_rampart_root_into_descriptor(tmp_path: Path) -> None:
    """A generic runtime config can declare where the RAMPART scripts live.

    Regression: a live Hermes start via --runtime-config died at the auth-proxy
    step because RuntimeLaunchConfig had no rampart_root field, so the backend
    fell back to Path('.') and could not find scripts/run_auth_proxy.py.
    """
    rampart_root = tmp_path / "rampart-here"
    runtime_config = RuntimeLaunchConfig(
        id="toy-agent",
        harness="rampart-docker",
        runtime_profile="toy-profile",
        rampart_root=rampart_root,
    )

    descriptor = build_descriptor_from_runtime_config(runtime_config)

    assert descriptor._rampart_path() == rampart_root.resolve()


def test_runtime_config_rampart_root_reaches_auth_proxy_step(tmp_path: Path) -> None:
    rampart_root = tmp_path / "rampart-scripts"
    (rampart_root / "scripts").mkdir(parents=True)
    (rampart_root / "scripts" / "run_auth_proxy.py").write_text("# proxy\n", encoding="utf-8")
    manifest_path = _write_fake_snapshot_manifest(tmp_path / "input", target="toy-agent")
    runtime_config = RuntimeLaunchConfig(
        id="toy-agent",
        harness="rampart-docker",
        runtime_profile="profile",
        required_paths=("profile/AGENTS.md",),
        launch_command=(sys.executable, "-c", "print('launch')"),
        rampart_root=rampart_root,
        endpoint_port=19000,
        sandbox_name="toy-sandbox",
    )

    backend = DockerSandboxBackend(descriptor=build_descriptor_from_runtime_config(runtime_config))
    result = backend.start(
        snapshot_manifest_path=manifest_path,
        target="toy-agent",
        output_dir=tmp_path / "sandbox-out",
        endpoint_url="http://127.0.0.1:19000",
        provider="mock",
        protocol="assert",
        dry_run=True,
        redact_paths=False,
    )

    state = json.loads(result.state_path.read_text(encoding="utf-8"))
    auth_step = next(step for step in state["plan"]["steps"] if step["name"] == "start_auth_proxy")
    assert str(rampart_root) in " ".join(auth_step["command"])


def test_generic_runtime_without_sandbox_launcher_rejects_live_start_before_host_steps(tmp_path: Path) -> None:
    manifest_path = _write_fake_snapshot_manifest(tmp_path / "input", target="toy-agent")
    runtime_config = RuntimeLaunchConfig(
        id="toy-agent",
        harness="rampart-docker",
        runtime_profile="profile",
        required_paths=("profile/AGENTS.md",),
        launch_command=None,
        endpoint_port=19000,
    )
    called: list[str] = []

    def run_step(step: LocalSandboxStep, log_path: Path) -> int:
        called.append(f"run:{step.name}")
        raise AssertionError(f"host run step should not execute: {step.name}")

    def start_step(step: LocalSandboxStep, log_path: Path) -> LocalSandboxManagedProcess:
        called.append(f"service:{step.name}")
        raise AssertionError(f"host service step should not execute: {step.name}")

    backend = DockerSandboxBackend(descriptor=build_descriptor_from_runtime_config(runtime_config))
    with pytest.raises(ValueError, match="sandbox launcher"):
        backend.start(
            snapshot_manifest_path=manifest_path,
            target="toy-agent",
            output_dir=tmp_path / "sandbox-out",
            endpoint_url="http://127.0.0.1:19000",
            provider="mock",
            model="toy-model",
            dry_run=False,
            run_step=run_step,
            start_step=start_step,
        )

    assert called == []



def test_docker_backend_runs_runtime_config_descriptor_without_openclaw_assumptions(tmp_path: Path) -> None:
    manifest_path = _write_fake_snapshot_manifest(tmp_path / "input", target="toy-agent")
    runtime_config = RuntimeLaunchConfig(
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

    backend = DockerSandboxBackend(descriptor=build_descriptor_from_runtime_config(runtime_config))
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


def test_runtime_config_rewrites_staged_model_routing_file(tmp_path: Path) -> None:
    manifest_path = _write_fake_snapshot_manifest(tmp_path / "input", target="toy-agent")
    staged_source_config = manifest_path.parent / "snapshot" / "profile" / "config.yaml"
    staged_source_config.write_text(
        "model:\n  provider: old\n  base_url: ''\n  api_key: old-key\n  api_mode: old-mode\n",
        encoding="utf-8",
    )
    runtime_config = RuntimeLaunchConfig(
        id="toy-agent",
        harness="rampart-docker",
        runtime_profile="profile",
        required_paths=("profile/AGENTS.md",),
        launch_command=(sys.executable, "-c", "print('launch')"),
        endpoint_port=19000,
        provider_route="copilot",
        model_routing=RuntimeModelRouting(
            staged_config_file="profile/config.yaml",
            provider_key="model.provider",
            base_url_key="model.base_url",
            api_key_key="model.api_key",
            api_mode_key="model.api_mode",
            resolved_provider="copilot",
            resolved_api_mode="codex_responses",
        ),
    )
    out = tmp_path / "sandbox-out"

    backend = DockerSandboxBackend(descriptor=build_descriptor_from_runtime_config(runtime_config))
    backend.start(
        snapshot_manifest_path=manifest_path,
        target="toy-agent",
        output_dir=out,
        endpoint_url="http://127.0.0.1:19000",
        provider="live",
        protocol="openai_chat",
        model="toy-model",
        dry_run=False,
        redact_paths=False,
        run_step=lambda step, log: (log.parent.mkdir(parents=True, exist_ok=True), log.write_text("ok\n"), 0)[-1],
        start_step=lambda step, log: (log.parent.mkdir(parents=True, exist_ok=True), log.write_text("ready\n"), LocalSandboxManagedProcess(name=step.name, pid=5100, log_path=log))[-1],
    )

    rewritten = yaml.safe_load((out / "sandbox" / "profile" / "config.yaml").read_text(encoding="utf-8"))
    assert rewritten["model"]["provider"] == "custom:assert-local-proxy"
    assert rewritten["providers"]["assert-local-proxy"]["base_url"] == "http://host.docker.internal:12435/copilot"
    assert rewritten["providers"]["assert-local-proxy"]["api_key"] == "proxy-managed"
    assert rewritten["providers"]["assert-local-proxy"]["transport"] == "codex_responses"
    assert rewritten["model"]["base_url"] == "http://host.docker.internal:12435/copilot"
    assert rewritten["model"]["api_key"] == "proxy-managed"
    assert rewritten["model"]["api_mode"] == "codex_responses"



def test_generic_launcher_receives_private_runtime_payload_files(tmp_path: Path) -> None:
    manifest_path = _write_fake_snapshot_manifest(tmp_path / "input", target="toy-agent")
    runtime_config = RuntimeLaunchConfig(
        id="toy-agent",
        harness="rampart-docker",
        runtime_profile="profile",
        required_paths=("profile/AGENTS.md",),
        launch_command=(
            sys.executable,
            "-c",
            "print('launch')",
            "--runtime-command",
            "{runtime_command_file}",
            "--identity-staging",
            "{identity_staging_file}",
        ),
        runtime_command=(str(tmp_path / "home" / "jake" / ".toy" / "venv" / "bin" / "python"), "-m", "toy_runtime", "serve"),
        identity_staging=(
            {"snapshot_path": "profile", "container_path": str(tmp_path / "home" / "jake" / ".toy")},
        ),
        endpoint_port=19000,
    )
    out = tmp_path / "sandbox-out"
    executed: list[list[str]] = []

    def run_step(step: LocalSandboxStep, log_path: Path) -> int:
        executed.append(step.command)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("ok\n", encoding="utf-8")
        return 0

    backend = DockerSandboxBackend(descriptor=build_descriptor_from_runtime_config(runtime_config))
    result = backend.start(
        snapshot_manifest_path=manifest_path,
        target="toy-agent",
        output_dir=out,
        endpoint_url="http://127.0.0.1:19000",
        provider="mock",
        protocol="assert",
        dry_run=False,
        redact_paths=True,
        run_step=run_step,
        start_step=lambda step, log: (log.parent.mkdir(parents=True, exist_ok=True), log.write_text("ready\n"), LocalSandboxManagedProcess(name=step.name, pid=5200, log_path=log))[-1],
    )

    runtime_file = out / "runtime-command.json"
    identity_file = out / "identity-staging.json"
    assert json.loads(runtime_file.read_text(encoding="utf-8")) == {
        "command": [str(tmp_path / "home" / "jake" / ".toy" / "venv" / "bin" / "python"), "-m", "toy_runtime", "serve"]
    }
    assert json.loads(identity_file.read_text(encoding="utf-8")) == {
        "entries": [{"snapshot_path": "profile", "container_path": str(tmp_path / "home" / "jake" / ".toy")}]
    }
    launch_command = next(command for command in executed if "--runtime-command" in command)
    assert str(runtime_file) in launch_command
    assert str(identity_file) in launch_command
    state_text = result.state_path.read_text(encoding="utf-8")
    assert str(tmp_path / "home" / "jake") not in state_text
    state = json.loads(state_text)
    assert state["plan"]["runtime_command"] == ["[LOCAL_PATH]", "-m", "toy_runtime", "serve"]
    assert state["plan"]["runtime_command_file"] == "[LOCAL_PATH]"
    assert state["plan"]["identity_staging_file"] == "[LOCAL_PATH]"



def test_runtime_config_live_provider_writes_auth_proxy_routes(tmp_path: Path) -> None:
    # The generic path must WRITE the auth-proxy routes file, not just reference it.
    # This is the exact live-run wall: the proxy step starts but has no routes,
    # so it exits and the health check fails. provider=copilot -> copilot route.
    rampart_root = tmp_path / "rampart-scripts"
    (rampart_root / "scripts").mkdir(parents=True)
    (rampart_root / "scripts" / "run_auth_proxy.py").write_text("# proxy\n", encoding="utf-8")
    manifest_path = _write_fake_snapshot_manifest(tmp_path / "input", target="toy-agent")
    runtime_config = RuntimeLaunchConfig(
        id="toy-agent",
        harness="rampart-docker",
        runtime_profile="profile",
        required_paths=("profile/AGENTS.md",),
        launch_command=(sys.executable, "-c", "print('launch')"),
        rampart_root=rampart_root,
        endpoint_port=19000,
        sandbox_name="toy-sandbox",
        provider_route="copilot",
    )
    out = tmp_path / "sandbox-out"

    backend = DockerSandboxBackend(descriptor=build_descriptor_from_runtime_config(runtime_config))
    result = backend.start(
        snapshot_manifest_path=manifest_path,
        target="toy-agent",
        output_dir=out,
        endpoint_url="http://127.0.0.1:19000",
        provider="live",
        protocol="openai_chat",
        model="toy-model",
        dry_run=False,
        redact_paths=False,
        run_step=lambda step, log: (log.parent.mkdir(parents=True, exist_ok=True), log.write_text("ok\n"), 0)[-1],
        start_step=lambda step, log: (log.parent.mkdir(parents=True, exist_ok=True), log.write_text("ready\n"), LocalSandboxManagedProcess(name=step.name, pid=5000, log_path=log))[-1],
    )

    # The auth-proxy step must point at a routes file that actually exists on disk.
    state = json.loads(result.state_path.read_text(encoding="utf-8"))
    auth_step = next(step for step in state["plan"]["steps"] if step["name"] == "start_auth_proxy")
    cmd = auth_step["command"]
    cfg_idx = cmd.index("--config") + 1
    routes_file = Path(cmd[cfg_idx])
    assert routes_file.exists(), f"auth-proxy routes file was not written: {routes_file}"
    payload = json.loads(routes_file.read_text(encoding="utf-8"))
    assert "copilot" in payload["providers"]


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


def test_docker_run_backend_mounts_identity_roots_and_maps_endpoint(tmp_path: Path) -> None:
    snapshot_root = tmp_path / "snapshot"
    (snapshot_root / ".hermes").mkdir(parents=True)
    (snapshot_root / ".hermes" / "config.yaml").write_text("model: {}\n", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "target": "hermes-default",
        "source": "agent_config",
        "snapshot_root": "snapshot",
        "copied_roots": [
            {"source": "[LOCAL_PATH]", "dest": ".hermes", "files_copied": 1, "bytes_copied": 10},
        ],
        "excluded_files": [],
    }
    manifest_path = tmp_path / "snapshot_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    # Uses a harmless command and a fake docker runner so the test verifies the
    # product contract without requiring Docker.
    docker_calls: list[list[str]] = []

    def fake_start(args, **kwargs):
        docker_calls.append(args)
        class Proc:
            pid = 12345
            def poll(self): return None
            def send_signal(self, sig): pass
            def wait(self, timeout=None): return 0
            def kill(self): pass
        return Proc()

    from assert_ai.local_sandbox import start_plain_docker_sandbox

    result = start_plain_docker_sandbox(
        snapshot_manifest_path=manifest_path,
        target="hermes-default",
        runtime_command=("python", "-m", "http.server", "8642"),
        identity_staging=(
            {"snapshot_path": ".hermes", "container_path": "/home/user/.hermes"},
        ),
        endpoint_url="http://127.0.0.1:18081/v1/chat/completions",
        runtime_port=8642,
        host_port=18081,
        health_url="http://127.0.0.1:18081/health",
        protocol="openai_chat",
        model="hermes-agent",
        output_dir=tmp_path / "out",
        docker_image="docker/sandbox-templates:shell",
        start_process=fake_start,
        wait_for_health=lambda url, timeout_seconds: None,
        redact_paths=True,
    )

    assert docker_calls, "docker run should be invoked"
    args = docker_calls[0]
    assert args[:3] == ["docker", "run", "--rm"]
    assert "-p" in args
    assert "18081:8642" in args
    assert "-v" in args
    mount_specs = [args[index + 1] for index, value in enumerate(args) if value == "-v"]
    assert any(spec.endswith(":/home/user/.hermes:rw") for spec in mount_specs)
    assert result.endpoint_url == "http://127.0.0.1:18081/v1/chat/completions"
    state = json.loads((tmp_path / "out" / "sandbox_state.json").read_text(encoding="utf-8"))
    assert state["backend"] == "docker-run"
    assert "/home/user" not in json.dumps(state)
    assert state["endpoint"]["protocol"] == "openai_chat"


def test_cli_sandbox_start_config_supports_docker_run_backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot_root = tmp_path / "snapshot"
    (snapshot_root / ".hermes").mkdir(parents=True)
    (snapshot_root / ".hermes" / "config.yaml").write_text("model: {}\n", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "target": "hermes-default",
        "source": "agent_config",
        "snapshot_root": "snapshot",
        "copied_roots": [{"source": "[LOCAL_PATH]", "dest": ".hermes", "files_copied": 1, "bytes_copied": 10}],
        "excluded_files": [],
    }
    manifest_path = tmp_path / "snapshot_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    config_path = tmp_path / "agent.yaml"
    config_path.write_text(
        """
id: hermes-default
roots:
  - source: /home/user/.hermes
launch:
  command: [python, -m, http.server, '8642']
endpoint:
  url: http://127.0.0.1:8642/v1/chat/completions
  protocol: openai_chat
  model: hermes-agent
""".strip()
        + "\n",
        encoding="utf-8",
    )

    calls = []

    def fake_start_plain(**kwargs):
        calls.append(kwargs)
        out = kwargs["output_dir"]
        out.mkdir(parents=True, exist_ok=True)
        state = out / "sandbox_state.json"
        target_cfg = out / "endpoint_target.yaml"
        state.write_text(json.dumps({"backend": "docker-run", "status": "running", "endpoint": {"url": kwargs["endpoint_url"]}}), encoding="utf-8")
        target_cfg.write_text("endpoint:\n  url: http://127.0.0.1:18081/v1/chat/completions\n", encoding="utf-8")
        class Result:
            state_path = state
            config_path = target_cfg
            endpoint_url = kwargs["endpoint_url"]
            process = None
        return Result()

    monkeypatch.setattr("assert_ai.local_sandbox.start_plain_docker_sandbox", fake_start_plain)
    result = CliRunner().invoke(
        cli,
        [
            "local", "sandbox", "start",
            "--snapshot", str(manifest_path),
            "--config", str(config_path),
            "--backend", "docker-run",
            "--output-dir", str(tmp_path / "out"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls
    assert calls[0]["runtime_port"] == 8642
    assert calls[0]["host_port"] == 18081
    assert calls[0]["endpoint_url"] == "http://127.0.0.1:18081/v1/chat/completions"
    assert calls[0]["container_env"]["HERMES_HOME"] == "/home/user/.hermes"
    env_file = calls[0]["api_key_env_file"]
    assert env_file.exists()
    assert env_file.stat().st_mode & 0o777 == 0o600
    assert env_file.read_text(encoding="utf-8").startswith("export ASSERT_LOCAL_AGENT_API_KEY_")



def test_cli_docker_run_sets_openclaw_home_for_openclaw_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest_path = _write_snapshot_manifest(tmp_path / "input", target="openclaw-main")
    config_path = tmp_path / "agent.yaml"
    config_path.write_text(
        """
id: openclaw-main
roots:
  - source: /home/user/.openclaw
launch:
  command: [node, /home/user/openclaw/dist/index.js, gateway, --port, "18789"]
endpoint:
  url: http://127.0.0.1:{endpoint_port}/v1/chat/completions
  protocol: openai_chat
  model: github-copilot/gpt-5.5
""".strip()
        + "\n",
        encoding="utf-8",
    )
    calls = []

    def fake_start_plain(**kwargs):
        calls.append(kwargs)
        out = kwargs["output_dir"]
        out.mkdir(parents=True, exist_ok=True)
        state = out / "sandbox_state.json"
        target_cfg = out / "endpoint_target.yaml"
        state.write_text(json.dumps({"backend": "docker-run", "status": "running", "endpoint": {"url": kwargs["endpoint_url"]}}), encoding="utf-8")
        target_cfg.write_text("endpoint:\n  url: http://127.0.0.1:18081/v1/chat/completions\n", encoding="utf-8")
        class Result:
            state_path = state
            config_path = target_cfg
            endpoint_url = kwargs["endpoint_url"]
            process = None
        return Result()

    monkeypatch.setattr("assert_ai.local_sandbox.start_plain_docker_sandbox", fake_start_plain)
    result = CliRunner().invoke(
        cli,
        [
            "local", "sandbox", "start",
            "--snapshot", str(manifest_path),
            "--config", str(config_path),
            "--backend", "docker-run",
            "--output-dir", str(tmp_path / "out"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls[0]["runtime_port"] == 18789
    assert calls[0]["host_port"] == 18081
    assert calls[0]["container_env"]["OPENCLAW_HOME"] == "/home/user"



def test_cli_docker_run_uses_endpoint_url_port_as_host_port(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest_path = _write_snapshot_manifest(tmp_path / "input", target="openclaw-main")
    config_path = tmp_path / "agent.yaml"
    config_path.write_text(
        """
id: openclaw-main
roots:
  - source: /home/user/.openclaw
launch:
  command: [node, /home/user/openclaw/dist/index.js, gateway, --port, "18789"]
endpoint:
  url: http://127.0.0.1:{endpoint_port}/v1/chat/completions
  protocol: openai_chat
  model: github-copilot/gpt-5.5
""".strip() + "\n", encoding="utf-8")
    calls=[]
    def fake_start_plain(**kwargs):
        calls.append(kwargs)
        out=kwargs["output_dir"]; out.mkdir(parents=True, exist_ok=True)
        state=out/"sandbox_state.json"; target_cfg=out/"endpoint_target.yaml"
        state.write_text(json.dumps({"backend":"docker-run","status":"running","endpoint":{"url":kwargs["endpoint_url"]}}), encoding="utf-8")
        target_cfg.write_text("endpoint:\n  url: " + kwargs["endpoint_url"] + "\n", encoding="utf-8")
        class Result:
            state_path=state; config_path=target_cfg; endpoint_url=kwargs["endpoint_url"]; process=None
        return Result()
    monkeypatch.setattr("assert_ai.local_sandbox.start_plain_docker_sandbox", fake_start_plain)
    result=CliRunner().invoke(cli,["local","sandbox","start","--snapshot",str(manifest_path),"--config",str(config_path),"--backend","docker-run","--endpoint-url","http://127.0.0.1:18082/v1/chat/completions","--output-dir",str(tmp_path/"out")])
    assert result.exit_code == 0, result.output
    assert calls[0]["host_port"] == 18082
    assert calls[0]["runtime_port"] == 18789
    assert calls[0]["endpoint_url"] == "http://127.0.0.1:18082/v1/chat/completions"


def test_docker_run_backend_adds_runtime_command_symlink_target_mount(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    interpreter_root = tmp_path / "uv" / "cpython-3.11-linux-x86_64-gnu"
    (runtime_root / "venv" / "bin").mkdir(parents=True)
    (interpreter_root / "bin").mkdir(parents=True)
    (interpreter_root / "lib" / "python3.11").mkdir(parents=True)
    python_bin = interpreter_root / "bin" / "python3.11"
    python_bin.write_text("python", encoding="utf-8")
    link = runtime_root / "venv" / "bin" / "python"
    link.symlink_to(python_bin)

    from assert_ai.local_sandbox import identity_mounts_for_runtime_command

    mounts = identity_mounts_for_runtime_command((str(link), "-m", "hermes_cli.main"))

    assert mounts == (
        {"host_path": str(interpreter_root), "container_path": str(interpreter_root), "mode": "ro"},
    )


def test_docker_run_backend_preserves_runtime_command_raw_symlink_alias(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    uv_root = tmp_path / "uv"
    real_root = uv_root / "cpython-3.11.15-linux-x86_64-gnu"
    alias_root = uv_root / "cpython-3.11-linux-x86_64-gnu"
    (runtime_root / "venv" / "bin").mkdir(parents=True)
    (real_root / "bin").mkdir(parents=True)
    (real_root / "lib" / "python3.11").mkdir(parents=True)
    python_bin = real_root / "bin" / "python3.11"
    python_bin.write_text("python", encoding="utf-8")
    alias_root.symlink_to(real_root)
    link = runtime_root / "venv" / "bin" / "python"
    link.symlink_to(alias_root / "bin" / "python3.11")

    from assert_ai.local_sandbox import identity_mounts_for_runtime_command

    mounts = identity_mounts_for_runtime_command((str(link), "-m", "hermes_cli.main"))

    assert mounts == (
        {"host_path": str(alias_root), "container_path": str(alias_root), "mode": "ro"},
    )


def test_docker_run_backend_passes_container_environment(tmp_path: Path) -> None:
    snapshot_root = tmp_path / "snapshot"
    (snapshot_root / ".hermes").mkdir(parents=True)
    manifest = {
        "schema_version": 1,
        "target": "hermes-default",
        "source": "agent_config",
        "snapshot_root": "snapshot",
        "copied_roots": [{"source": "[LOCAL_PATH]", "dest": ".hermes", "files_copied": 0, "bytes_copied": 0}],
        "excluded_files": [],
    }
    manifest_path = tmp_path / "snapshot_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    docker_calls = []
    def fake_start(args, **kwargs):
        docker_calls.append(args)
        class Proc:
            pid = 12345
            def poll(self): return None
            def send_signal(self, sig): pass
            def wait(self, timeout=None): return 0
            def kill(self): pass
        return Proc()
    from assert_ai.local_sandbox import start_plain_docker_sandbox
    start_plain_docker_sandbox(
        snapshot_manifest_path=manifest_path,
        target="hermes-default",
        runtime_command=("sleep", "999"),
        identity_staging=({"snapshot_path": ".hermes", "container_path": "/home/user/.hermes"},),
        endpoint_url="http://127.0.0.1:18081/v1/chat/completions",
        runtime_port=8642,
        host_port=18081,
        health_url=None,
        output_dir=tmp_path / "out",
        model="hermes-agent",
        container_env={"API_SERVER_ENABLED": "true", "API_SERVER_KEY": "probe-secret"},
        start_process=fake_start,
    )
    args = docker_calls[0]
    env_pairs = [args[index + 1] for index, value in enumerate(args) if value == "-e"]
    assert "API_SERVER_ENABLED=true" in env_pairs
    assert "API_SERVER_KEY=probe-secret" in env_pairs


def test_docker_run_backend_rewrites_model_routing_and_starts_auth_proxy(tmp_path: Path) -> None:
    snapshot_root = tmp_path / "snapshot"
    (snapshot_root / ".hermes").mkdir(parents=True)
    (snapshot_root / ".hermes" / "config.yaml").write_text(
        "model:\n  provider: copilot\n  base_url: ''\n  api_key: ''\n  api_mode: github-copilot\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "target": "hermes-default",
        "source": "agent_config",
        "snapshot_root": "snapshot",
        "copied_roots": [{"source": "[LOCAL_PATH]", "dest": ".hermes", "files_copied": 1, "bytes_copied": 10}],
        "excluded_files": [],
    }
    manifest_path = tmp_path / "snapshot_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    starts: list[list[str]] = []

    def fake_start(args, **kwargs):
        starts.append(args)
        class Proc:
            pid = 12345 + len(starts)
            def poll(self): return None
            def send_signal(self, sig): pass
            def wait(self, timeout=None): return 0
            def kill(self): pass
        return Proc()

    from assert_ai.local_sandbox import RuntimeModelRouting, start_plain_docker_sandbox

    start_plain_docker_sandbox(
        snapshot_manifest_path=manifest_path,
        target="hermes-default",
        runtime_command=("sleep", "999"),
        identity_staging=({"snapshot_path": ".hermes", "container_path": "/home/user/.hermes"},),
        endpoint_url="http://127.0.0.1:18081/v1/chat/completions",
        runtime_port=8642,
        host_port=18081,
        health_url=None,
        output_dir=tmp_path / "out",
        model="hermes-agent",
        model_routing=RuntimeModelRouting(
            staged_config_file=".hermes/config.yaml",
            provider_key="model.provider",
            base_url_key="model.base_url",
            api_key_key="model.api_key",
            api_mode_key="model.api_mode",
            resolved_provider="copilot",
            resolved_api_mode="github-copilot",
        ),
        auth_proxy_port=12435,
        provider_route="copilot",
        start_process=fake_start,
        wait_for_health=lambda url, timeout_seconds: None,
    )

    assert len(starts) == 2
    auth_proxy_args = starts[0]
    docker_args = starts[1]
    assert "run_auth_proxy.py" in " ".join(auth_proxy_args)
    assert "--config" in auth_proxy_args
    assert "docker" == docker_args[0]
    rewritten = yaml.safe_load((snapshot_root / ".hermes" / "config.yaml").read_text(encoding="utf-8"))
    assert rewritten["model"]["base_url"] == "http://host.docker.internal:12435/copilot"
    assert rewritten["model"]["api_key"] == "proxy-managed"
    state = json.loads((tmp_path / "out" / "sandbox_state.json").read_text(encoding="utf-8"))
    assert {proc["name"] for proc in state["processes"]} == {"auth-proxy", "docker-run"}
