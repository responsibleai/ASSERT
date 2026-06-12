# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Local agent sandbox startup helpers.

This module owns the product-shaped contract for turning a copied local-agent
snapshot into a running endpoint target. The first backend is intentionally
minimal and generic: it stages the snapshot into a disposable work directory,
starts a user-provided command, waits for an optional health endpoint, and writes
state/config artifacts that the normal ASSERT pipeline can consume.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import time
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

import yaml

from assert_ai.core.io import write_json
from assert_ai.core.security import validate_endpoint_url


@dataclass(frozen=True)
class LocalSandboxStep:
    """One setup step in a local sandbox backend plan."""

    name: str
    command: list[str]
    cwd: Path
    kind: str = "run"  # run | service
    env: dict[str, str] = field(default_factory=dict)
    timeout_seconds: int = 600
    health_url: str | None = None
    log_name: str | None = None


@dataclass(frozen=True)
class LocalSandboxManagedProcess:
    """Process record returned by a local sandbox service step."""

    name: str
    pid: int
    log_path: Path
    process: subprocess.Popen[str] | None = None


@dataclass(frozen=True)
class LocalSandboxCleanupCommand:
    """Cleanup command for sandbox resources that outlive local service PIDs."""

    name: str
    command: list[str]
    timeout_seconds: int = 120


@dataclass(frozen=True)
class LocalSandboxStartResult:
    """Result for a started local sandbox endpoint."""

    state_path: Path
    config_path: Path
    sandbox_root: Path
    endpoint_url: str
    process: subprocess.Popen[str] | None = None

    def stop(self) -> None:
        """Terminate the started command if it is still running."""
        if self.process is None:
            return
        if self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)


def _load_snapshot_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read snapshot manifest: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("snapshot manifest must be a JSON object")
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported snapshot manifest schema_version")
    snapshot_root = payload.get("snapshot_root")
    if not isinstance(snapshot_root, str) or not snapshot_root:
        raise ValueError("snapshot manifest must include snapshot_root")
    return payload


def _path_value(path: Path | None, *, redact_paths: bool) -> str | None:
    if path is None:
        return None
    return "[LOCAL_PATH]" if redact_paths else str(path)


def _stage_snapshot(*, manifest_path: Path, manifest: dict[str, Any], output_dir: Path) -> Path:
    source_root = (manifest_path.parent / str(manifest["snapshot_root"])).resolve()
    if not source_root.exists() or not source_root.is_dir():
        raise ValueError(f"snapshot root does not exist: {source_root}")
    sandbox_root = output_dir / "sandbox"
    if sandbox_root.exists():
        shutil.rmtree(sandbox_root)
    shutil.copytree(source_root, sandbox_root, symlinks=False)
    return sandbox_root


def _format_url(template: str, *, port: str | None) -> str:
    if "{port}" in template:
        if not port:
            raise ValueError("endpoint URL uses {port}, but the sandbox command did not report a port")
        return template.format(port=port)
    return template


def _read_reported_port(process: subprocess.Popen[str], *, timeout_seconds: float = 10.0) -> str | None:
    if process.stdout is None:
        return None
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise ValueError("sandbox command exited before reporting a port")
        line = process.stdout.readline()
        if line:
            value = line.strip()
            if value:
                if not value.isdigit():
                    raise ValueError(f"sandbox command reported a non-numeric port: {value}")
                return value
        time.sleep(0.05)
    raise ValueError("timed out waiting for sandbox command to report a port")


def _wait_for_health(url: str, *, timeout_seconds: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=2) as response:  # noqa: S310 - URL is validated as local-dev before use.
                if 200 <= response.status < 300:
                    return
        except (OSError, URLError) as exc:
            last_error = exc
        time.sleep(0.2)
    detail = f": {last_error}" if last_error else ""
    raise ValueError(f"sandbox health check failed for {url}{detail}")


def _endpoint_config(
    *,
    endpoint_url: str,
    protocol: str,
    model: str | None,
    api_key_env: str | None,
    stream: bool,
) -> dict[str, Any]:
    if protocol not in {"assert", "openai_chat"}:
        raise ValueError("protocol must be one of: assert, openai_chat")
    if protocol == "openai_chat" and not model:
        raise ValueError("model is required when protocol is openai_chat")
    endpoint: dict[str, Any] = {
        "url": endpoint_url,
        "protocol": protocol,
        "stream": bool(stream),
        "local_dev": True,
    }
    if model:
        endpoint["model"] = model
    if api_key_env:
        endpoint["api_key_env"] = api_key_env
    return endpoint


def _write_endpoint_target_config(path: Path, endpoint: dict[str, Any]) -> None:
    payload = {"pipeline": {"inference": {"target": {"endpoint": endpoint}}}}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _openclaw_docker_plan_steps(*, provider: str) -> list[dict[str, Any]]:
    steps = [
        {"name": "preflight", "kind": "run"},
        {"name": "prepare_openclaw_runtime_archive", "kind": "run"},
    ]
    if provider == "mock":
        steps.append({"name": "start_mock_openai", "kind": "service"})
    steps.extend(
        [
            {"name": "start_auth_proxy", "kind": "service"},
            {"name": "launch_openclaw_sandbox", "kind": "run"},
            {"name": "start_openclaw_endpoint_bridge", "kind": "service"},
        ]
    )
    return steps


def _default_runner_root() -> Path:
    return Path(__file__).resolve().parent / "local_sandbox_runtime"


def _default_rampart_root() -> Path:
    return Path.home() / "LocalOps" / "rampart-examples" / "openclaw"


def _rampart_python(rampart_root: Path) -> str:
    candidate = rampart_root / ".venv" / "bin" / "python"
    return str(candidate) if candidate.exists() else sys.executable


def _parse_model_ref(value: str) -> tuple[str, str, str]:
    raw = value.strip()
    if not raw or "/" not in raw:
        raise ValueError("model ref must look like provider/model or provider/model=Display Name")
    left, _, display = raw.partition("=")
    provider, _, model_id = left.partition("/")
    provider = provider.strip()
    model_id = model_id.strip()
    display_name = display.strip() or model_id
    if not provider or not model_id:
        raise ValueError("model ref must include non-empty provider and model id")
    return provider, model_id, display_name


def _models_json(model_ref: str) -> str:
    provider, model_id, display_name = _parse_model_ref(model_ref)
    return json.dumps({provider: [{"id": model_id, "name": display_name}]})


def _path_has_file(path: Path, *parts: str) -> bool:
    return (path.joinpath(*parts)).exists()


def _find_staged_workspace(sandbox_root: Path) -> Path:
    candidates = [
        sandbox_root / ".openclaw" / "workspace",
        sandbox_root / "workspace",
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            return candidate
    raise ValueError("staged snapshot does not contain an OpenClaw workspace")


def _find_staged_openclaw_runtime(sandbox_root: Path) -> Path:
    candidates = [
        sandbox_root / "runtime" / "openclaw-package",
        sandbox_root / "openclaw-package",
    ]
    for candidate in candidates:
        if _path_has_file(candidate, "package.json") and _path_has_file(candidate, "openclaw.mjs"):
            return candidate
    raise ValueError("staged snapshot does not contain runtime/openclaw-package with package.json/openclaw.mjs")


def _ensure_source_bundle(workspace: Path) -> Path:
    source_bundle = workspace / "source-bundle.json"
    if source_bundle.exists():
        return source_bundle
    includes = [
        name
        for name in ("AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md", "MEMORY.md")
        if (workspace / name).exists()
    ]
    source_bundle.write_text(json.dumps({"include": includes, "exclude_patterns": []}, indent=2) + "\n", encoding="utf-8")
    return source_bundle


def _write_runtime_archive(runtime_path: Path, output: Path) -> None:
    if not _path_has_file(runtime_path, "package.json") or not _path_has_file(runtime_path, "openclaw.mjs"):
        raise ValueError(f"OpenClaw runtime path is missing package.json/openclaw.mjs: {runtime_path}")
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(output.suffix + ".tmp")
    if tmp.exists():
        tmp.unlink()
    with tarfile.open(tmp, "w:gz") as tar:
        tar.add(runtime_path, arcname="openclaw")
    tmp.replace(output)


def _write_mock_auth_proxy_config(path: Path, *, mock_openai_port: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "providers": {
            "openai": {
                "enabled": True,
                "api_key": "***",
                "base_url": f"http://127.0.0.1:{mock_openai_port}/v1",
                "auth": "bearer",
                "path_prefix": "/openai",
            }
        }
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _default_run_step(step: LocalSandboxStep, log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(step.env)
    completed = subprocess.run(
        step.command,
        cwd=step.cwd,
        env=env,
        text=True,
        capture_output=True,
        timeout=step.timeout_seconds,
        check=False,
    )
    log_path.write_text((completed.stdout or "") + (completed.stderr or ""), encoding="utf-8")
    return int(completed.returncode)


def _default_start_step(step: LocalSandboxStep, log_path: Path) -> LocalSandboxManagedProcess:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(step.env)
    log_handle = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        step.command,
        cwd=step.cwd,
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    if step.health_url:
        try:
            _wait_for_health(step.health_url, timeout_seconds=45)
        except Exception:
            if process.poll() is not None:
                raise
            # Docker Sandbox can briefly report its VHD as locked immediately
            # after launch. Give bridge-style services a second chance before
            # treating startup as failed.
            time.sleep(5)
            _wait_for_health(step.health_url, timeout_seconds=45)
    return LocalSandboxManagedProcess(name=step.name, pid=process.pid, log_path=log_path, process=process)


def _default_cleanup_step(command: list[str], log_path: Path, timeout_seconds: int) -> int:
    if not command:
        return 0
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        completed = subprocess.run(command, text=True, capture_output=True, timeout=timeout_seconds, check=False)
        log_path.write_text((completed.stdout or "") + (completed.stderr or ""), encoding="utf-8")
        return int(completed.returncode)
    except Exception as exc:  # pragma: no cover - cleanup cannot be allowed to mask primary errors
        log_path.write_text(f"cleanup failed: {type(exc).__name__}: {exc}\n", encoding="utf-8")
        return 1


def _terminate_process_group_status(pid: int, *, timeout_seconds: float = 5.0) -> str:
    if _pid_has_exited(pid):
        return "already_exited"
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return "already_exited"
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if _pid_has_exited(pid):
            return "stopped"
        time.sleep(0.05)
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return "already_exited"
    return "killed"


def _redact_local_string(value: str, *, redact_paths: bool) -> str:
    if not redact_paths:
        return value
    expanded_home = str(Path.home())
    if value.startswith("/") or expanded_home in value or "\\wsl.localhost" in value:
        return "[LOCAL_PATH]"
    return value


def _redact_command(command: list[str], *, redact_paths: bool) -> list[str]:
    return [_redact_local_string(part, redact_paths=redact_paths) for part in command]


def _local_sandbox_step_json(step: LocalSandboxStep, *, redact_paths: bool) -> dict[str, Any]:
    return {
        "name": step.name,
        "kind": step.kind,
        "command": _redact_command(step.command, redact_paths=redact_paths),
        "cwd": _path_value(step.cwd, redact_paths=redact_paths),
        "health_url": step.health_url,
        "timeout_seconds": step.timeout_seconds,
    }


def _local_cleanup_json(command: LocalSandboxCleanupCommand, *, redact_paths: bool) -> dict[str, Any]:
    return {
        "name": command.name,
        "command": _redact_command(command.command, redact_paths=redact_paths),
        "timeout_seconds": command.timeout_seconds,
    }


def start_openclaw_docker_sandbox(
    *,
    snapshot_manifest_path: str | Path,
    target: str,
    output_dir: str | Path,
    runner_root: str | Path | None = None,
    rampart_root: str | Path | None = None,
    sandbox_name: str = "oc-local-agent",
    provider: str = "mock",
    model_ref: str = "openai/mock-model=Mock Model",
    endpoint_port: int = 18081,
    auth_proxy_port: int = 12435,
    mock_openai_port: int = 18080,
    docker_command: str = "docker.exe",
    skip_build: bool = False,
    auth_proxy_config: str | Path | None = None,
    protocol: str = "assert",
    model: str | None = None,
    api_key_env: str | None = None,
    stream: bool = False,
    redact_paths: bool = True,
    dry_run: bool = False,
    run_step: Any | None = None,
    start_step: Any | None = None,
    stop_process: Any | None = None,
    cleanup_step: Any | None = None,
) -> LocalSandboxStartResult:
    """Prepare or start an OpenClaw Docker sandbox using the product state contract."""

    if target != "openclaw":
        raise ValueError("the docker backend currently supports target openclaw only")
    if provider not in {"mock", "live"}:
        raise ValueError("provider must be one of: mock, live")
    if provider == "live" and auth_proxy_config is None:
        raise ValueError("provider live requires --auth-proxy-config")

    run_step_fn = run_step or _default_run_step
    start_step_fn = start_step or _default_start_step
    stop_process_fn = stop_process or (lambda pid, timeout_seconds: _terminate_process_group_status(pid, timeout_seconds=timeout_seconds))
    cleanup_step_fn = cleanup_step or _default_cleanup_step

    manifest_path = Path(snapshot_manifest_path).expanduser().resolve()
    output = Path(output_dir).expanduser().resolve()
    manifest = _load_snapshot_manifest(manifest_path)
    if manifest.get("target") != target:
        raise ValueError(f"snapshot manifest target is {manifest.get('target')!r}, not {target!r}")

    endpoint_url = f"http://127.0.0.1:{endpoint_port}"
    endpoint = _endpoint_config(
        endpoint_url=endpoint_url,
        protocol=protocol,
        model=model,
        api_key_env=api_key_env,
        stream=stream,
    )
    validate_endpoint_url(endpoint["url"], allow_localhost=True)
    output.mkdir(parents=True, exist_ok=True)
    sandbox_root = _stage_snapshot(manifest_path=manifest_path, manifest=manifest, output_dir=output)
    workspace = _find_staged_workspace(sandbox_root)
    runtime_path = _find_staged_openclaw_runtime(sandbox_root)
    source_bundle = _ensure_source_bundle(workspace)
    state_path = output / "sandbox_state.json"
    config_path = output / "endpoint_target.yaml"
    logs_dir = output / "logs"
    runtime_archive = output / "runtime" / "openclaw-runtime.tar.gz"
    request_log = output / "auth-proxy-requests.jsonl"
    runner_path = Path(runner_root).expanduser().resolve() if runner_root else _default_runner_root()
    rampart_path = Path(rampart_root).expanduser().resolve() if rampart_root else _default_rampart_root()
    mock_config_path = output / "mock-auth-proxy.json"
    resolved_auth_proxy_config = Path(auth_proxy_config).expanduser().resolve() if auth_proxy_config else mock_config_path
    rampart_python = _rampart_python(rampart_path)
    launcher = runner_path / "start_openclaw_sandbox.ps1"
    mock_server = runner_path / "mock_openai_server.py"
    endpoint_bridge = runner_path / "openclaw_endpoint_bridge.py"
    setup_helpers = [launcher, endpoint_bridge]
    if provider == "mock":
        setup_helpers.append(mock_server)
    for helper in setup_helpers:
        if not helper.exists():
            raise ValueError(f"local sandbox helper is missing: {helper}")
    if not (rampart_path / "scripts" / "openclaw-sandbox.ps1").exists():
        raise ValueError(f"OpenClaw Docker sandbox launcher not found under: {rampart_path}")
    if not (rampart_path / "scripts" / "run_auth_proxy.py").exists():
        raise ValueError(f"OpenClaw auth proxy runner not found under: {rampart_path}")
    if provider == "mock" and not auth_proxy_config:
        _write_mock_auth_proxy_config(mock_config_path, mock_openai_port=mock_openai_port)

    model_json = _models_json(model_ref)
    steps: list[LocalSandboxStep] = [
        LocalSandboxStep(
            name="preflight",
            command=[sys.executable, "-c", "import pathlib, sys; sys.exit(0)"],
            cwd=output,
            timeout_seconds=120,
        ),
        LocalSandboxStep(
            name="prepare_openclaw_runtime_archive",
            command=[sys.executable, "-c", "import pathlib, sys; sys.exit(0)"],
            cwd=output,
            timeout_seconds=300,
        ),
    ]
    if provider == "mock":
        steps.append(
            LocalSandboxStep(
                name="start_mock_openai",
                command=[sys.executable, str(mock_server), "--port", str(mock_openai_port)],
                cwd=runner_path,
                kind="service",
                health_url=f"http://127.0.0.1:{mock_openai_port}/health",
                log_name="mock-openai.log",
            )
        )
    steps.extend(
        [
            LocalSandboxStep(
                name="start_auth_proxy",
                command=[
                    rampart_python,
                    str(rampart_path / "scripts" / "run_auth_proxy.py"),
                    "serve",
                    "--config",
                    str(resolved_auth_proxy_config),
                    "--port",
                    str(auth_proxy_port),
                    "--request-log",
                    str(request_log),
                    "-v",
                ],
                cwd=rampart_path,
                kind="service",
                health_url=f"http://127.0.0.1:{auth_proxy_port}/health",
                log_name="auth-proxy.log",
            ),
            LocalSandboxStep(
                name="launch_openclaw_sandbox",
                command=[
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(launcher),
                    "-RampartOpenClawRoot",
                    str(rampart_path),
                    "-SandboxName",
                    sandbox_name,
                    "-Models",
                    model_json,
                    "-AuthProxyPort",
                    str(auth_proxy_port),
                    "-RuntimeArchive",
                    str(runtime_archive),
                    *( ["-SkipBuild"] if skip_build else [] ),
                ],
                cwd=runner_path,
                timeout_seconds=900,
                log_name="launch-openclaw-sandbox.log",
            ),
            LocalSandboxStep(
                name="start_openclaw_endpoint_bridge",
                command=[
                    rampart_python,
                    str(endpoint_bridge),
                    "--sandbox-name",
                    sandbox_name,
                    "--workspace",
                    str(workspace),
                    "--rampart-root",
                    str(rampart_path),
                    "--port",
                    str(endpoint_port),
                    "--docker-command",
                    docker_command,
                ],
                cwd=runner_path,
                kind="service",
                health_url=f"{endpoint_url}/health",
                log_name="openclaw-endpoint-bridge.log",
                env={"ASSERT_DOCKER_COMMAND": docker_command, "RAMPART_OPENCLAW_ROOT": str(rampart_path)},
            ),
        ]
    )
    cleanup_commands = [
        LocalSandboxCleanupCommand("sandbox_stop", [docker_command, "sandbox", "stop", sandbox_name]),
        LocalSandboxCleanupCommand("sandbox_rm", [docker_command, "sandbox", "rm", sandbox_name]),
    ]
    plan_steps = [_local_sandbox_step_json(step, redact_paths=redact_paths) for step in steps]
    cleanup_json = [_local_cleanup_json(command, redact_paths=redact_paths) for command in cleanup_commands]
    state = {
        "schema_version": 1,
        "target": target,
        "backend": "docker",
        "status": "planned" if dry_run else "starting",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "snapshot": {
            "manifest": _path_value(manifest_path, redact_paths=redact_paths),
            "snapshot_root": str(manifest.get("snapshot_root")),
            "copied_roots": manifest.get("copied_roots", []),
        },
        "sandbox_root": "sandbox" if redact_paths else str(sandbox_root),
        "endpoint": endpoint,
        "plan": {
            "sandbox_name": sandbox_name,
            "provider": provider,
            "model_ref": model_ref,
            "endpoint_port": endpoint_port,
            "auth_proxy_port": auth_proxy_port,
            "mock_openai_port": mock_openai_port if provider == "mock" else None,
            "runner": "openclaw-docker-sandbox",
            "runner_root": _path_value(runner_path, redact_paths=redact_paths),
            "rampart_root": _path_value(rampart_path, redact_paths=redact_paths),
            "workspace": _path_value(workspace, redact_paths=redact_paths),
            "source_bundle": _path_value(source_bundle, redact_paths=redact_paths),
            "runtime_archive": _path_value(runtime_archive, redact_paths=redact_paths),
            "steps": plan_steps,
        },
        "processes": [],
        "cleanup": {
            "sandbox_name": sandbox_name,
            "required": not dry_run,
            "commands": cleanup_json,
        },
        "safety": {
            "live_home_mount": False,
            "snapshot_staged": True,
            "endpoint_local_dev": True,
            "external_services_proxied_or_mocked": True,
        },
    }
    write_json(state_path, state)
    _write_endpoint_target_config(config_path, endpoint)
    if dry_run:
        return LocalSandboxStartResult(
            state_path=state_path,
            config_path=config_path,
            sandbox_root=sandbox_root,
            endpoint_url=endpoint_url,
            process=None,
        )

    managed: list[LocalSandboxManagedProcess] = []
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
        _write_runtime_archive(runtime_path, runtime_archive)
        for step in steps:
            log_path = logs_dir / (step.log_name or f"{step.name}.log")
            if step.kind == "service":
                managed.append(start_step_fn(step, log_path))
                continue
            rc = int(run_step_fn(step, log_path))
            if rc != 0:
                raise ValueError(f"{step.name} failed with exit code {rc}; see {log_path}")
        state["status"] = "running"
        state["started_at"] = datetime.now(timezone.utc).isoformat()
        state["processes"] = [
            {
                "name": process.name,
                "pid": process.pid,
                "log_path": _path_value(process.log_path, redact_paths=redact_paths),
            }
            for process in managed
        ]
        write_json(state_path, state)
        return LocalSandboxStartResult(
            state_path=state_path,
            config_path=config_path,
            sandbox_root=sandbox_root,
            endpoint_url=endpoint_url,
            process=managed[-1].process if managed else None,
        )
    except Exception:
        for process in reversed(managed):
            stop_process_fn(process.pid, 5.0)
        cleanup_dir = logs_dir / "cleanup"
        for command in cleanup_commands:
            cleanup_step_fn(command.command, cleanup_dir / f"{command.name}.log", command.timeout_seconds)
        raise


def smoke_local_sandbox(
    state_path: str | Path,
    *,
    message: str = "Reply exactly: ok",
    timeout_seconds: float = 240.0,
) -> dict[str, Any]:
    """Send a minimal request to a started sandbox endpoint."""

    state_file = Path(state_path).expanduser().resolve()
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read sandbox state: {state_file}") from exc
    endpoint = state.get("endpoint")
    if not isinstance(endpoint, dict) or not isinstance(endpoint.get("url"), str):
        raise ValueError("sandbox state must include endpoint.url")
    endpoint_url = endpoint["url"]
    validate_endpoint_url(endpoint_url, allow_localhost=True)
    payload = json.dumps({"message": message, "history": []}).encode("utf-8")
    request = Request(
        endpoint_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - URL validated as local-dev.
            body = response.read().decode("utf-8")
    except (OSError, URLError) as exc:
        raise ValueError(f"sandbox smoke request failed for {endpoint_url}: {exc}") from exc
    try:
        response_json = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError("sandbox smoke response was not JSON") from exc
    if not isinstance(response_json, dict):
        raise ValueError("sandbox smoke response must be a JSON object")
    final_response = response_json.get("response")
    return {
        "schema_version": 1,
        "target": state.get("target"),
        "agent_endpoint": endpoint_url,
        "status": "ok" if isinstance(final_response, str) and final_response else "empty_response",
        "response": final_response,
        "events": response_json.get("events", []),
        "metadata": response_json.get("metadata", {}),
    }


def _pid_has_exited(pid: int) -> bool:
    try:
        waited_pid, _status = os.waitpid(pid, os.WNOHANG)
        if waited_pid == pid:
            return True
    except ChildProcessError:
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def stop_local_sandbox(state_path: str | Path, *, timeout_seconds: float = 5.0) -> dict[str, Any]:
    """Terminate processes recorded in a sandbox state file and mark it stopped."""

    state_file = Path(state_path).expanduser().resolve()
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read sandbox state: {state_file}") from exc
    processes = state.get("processes")
    if not isinstance(processes, list):
        raise ValueError("sandbox state must include a processes list")

    stopped: list[dict[str, Any]] = []
    for process_record in processes:
        if not isinstance(process_record, dict):
            continue
        pid = process_record.get("pid")
        name = str(process_record.get("name") or "process")
        if not isinstance(pid, int):
            continue
        if _pid_has_exited(pid):
            stopped.append({"name": name, "pid": pid, "status": "already_exited"})
            continue
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            stopped.append({"name": name, "pid": pid, "status": "already_exited"})
            continue
        except PermissionError as exc:
            raise ValueError(f"could not terminate sandbox process {pid}: {exc}") from exc
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if _pid_has_exited(pid):
                stopped.append({"name": name, "pid": pid, "status": "stopped"})
                break
            time.sleep(0.05)
        else:
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stopped.append({"name": name, "pid": pid, "status": "killed"})

    cleanup_results: list[dict[str, Any]] = []
    cleanup = state.get("cleanup")
    cleanup_commands = cleanup.get("commands") if isinstance(cleanup, dict) else None
    cleanup_dir = state_file.parent / "logs" / "cleanup"
    if isinstance(cleanup_commands, list):
        for item in cleanup_commands:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "cleanup")
            command = item.get("command")
            timeout = item.get("timeout_seconds", 120)
            if not isinstance(command, list) or not all(isinstance(part, str) for part in command):
                continue
            exit_code = _default_cleanup_step(command, cleanup_dir / f"{name}.log", int(timeout) if isinstance(timeout, int) else 120)
            cleanup_results.append({"name": name, "exit_code": exit_code})

    state["status"] = "stopped"
    state["stopped_at"] = datetime.now(timezone.utc).isoformat()
    state["stopped_processes"] = stopped
    state["cleanup_results"] = cleanup_results
    write_json(state_file, state)
    return {
        "schema_version": 1,
        "target": state.get("target"),
        "status": "stopped",
        "state": str(state_file),
        "processes": stopped,
        "cleanup_results": cleanup_results,
    }


def start_local_sandbox(
    *,
    snapshot_manifest_path: str | Path,
    target: str,
    backend: str,
    command: str,
    endpoint_url: str,
    health_url: str | None,
    protocol: str = "assert",
    model: str | None = None,
    api_key_env: str | None = None,
    stream: bool = False,
    output_dir: str | Path,
    redact_paths: bool = True,
    health_timeout_seconds: float = 30.0,
) -> LocalSandboxStartResult:
    """Stage a snapshot, start a command-backed sandbox endpoint, and write state.

    The command backend expects a long-running process. If ``endpoint_url`` or
    ``health_url`` contains ``{port}``, the command must print the chosen port on
    its first stdout line.
    """

    if backend != "command":
        raise ValueError("only the command backend is implemented")
    if not command.strip():
        raise ValueError("--command is required for the command backend")

    manifest_path = Path(snapshot_manifest_path).expanduser().resolve()
    output = Path(output_dir).expanduser().resolve()
    manifest = _load_snapshot_manifest(manifest_path)
    if manifest.get("target") != target:
        raise ValueError(f"snapshot manifest target is {manifest.get('target')!r}, not {target!r}")

    # Validate protocol/model before starting any process.
    endpoint_template_needs_port = "{port}" in endpoint_url or (health_url is not None and "{port}" in health_url)
    endpoint_for_validation = endpoint_url.replace("{port}", "9")
    endpoint = _endpoint_config(
        endpoint_url=endpoint_for_validation,
        protocol=protocol,
        model=model,
        api_key_env=api_key_env,
        stream=stream,
    )
    validate_endpoint_url(endpoint["url"], allow_localhost=True)

    output.mkdir(parents=True, exist_ok=True)
    sandbox_root = _stage_snapshot(manifest_path=manifest_path, manifest=manifest, output_dir=output)
    logs_dir = output / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stderr_log = logs_dir / "sandbox-command.stderr.log"

    env = None
    args = shlex.split(command)
    if not args:
        raise ValueError("--command is required for the command backend")
    process: subprocess.Popen[str] | None = None
    try:
        with stderr_log.open("w", encoding="utf-8") as stderr_handle:
            process = subprocess.Popen(
                args,
                cwd=sandbox_root,
                env=env,
                stdout=subprocess.PIPE,
                stderr=stderr_handle,
                text=True,
                start_new_session=True,
            )

        reported_port = _read_reported_port(process) if endpoint_template_needs_port else None
        resolved_endpoint_url = _format_url(endpoint_url, port=reported_port)
        resolved_health_url = _format_url(health_url, port=reported_port) if health_url else None
        endpoint = _endpoint_config(
            endpoint_url=resolved_endpoint_url,
            protocol=protocol,
            model=model,
            api_key_env=api_key_env,
            stream=stream,
        )
        validate_endpoint_url(endpoint["url"], allow_localhost=True)
        if resolved_health_url:
            validate_endpoint_url(resolved_health_url, allow_localhost=True)
            _wait_for_health(resolved_health_url, timeout_seconds=health_timeout_seconds)

        state_path = output / "sandbox_state.json"
        config_path = output / "endpoint_target.yaml"
        state = {
            "schema_version": 1,
            "target": target,
            "backend": backend,
            "status": "running",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "snapshot": {
                "manifest": _path_value(manifest_path, redact_paths=redact_paths),
                "snapshot_root": str(manifest.get("snapshot_root")),
                "copied_roots": manifest.get("copied_roots", []),
            },
            "sandbox_root": "sandbox" if redact_paths else str(sandbox_root),
            "endpoint": endpoint,
            "health_url": resolved_health_url,
            "processes": [
                {
                    "name": "sandbox-command",
                    "pid": process.pid,
                    "log_path": _path_value(stderr_log, redact_paths=redact_paths),
                }
            ],
            "cleanup": {
                "stop": f"kill {process.pid}",
            },
        }
        write_json(state_path, state)
        _write_endpoint_target_config(config_path, endpoint)
        return LocalSandboxStartResult(
            state_path=state_path,
            config_path=config_path,
            sandbox_root=sandbox_root,
            endpoint_url=resolved_endpoint_url,
            process=process,
        )
    except Exception:
        if process is not None and process.poll() is None:
            try:
                process.send_signal(signal.SIGTERM)
                process.wait(timeout=5)
            except Exception:
                process.kill()
                process.wait(timeout=5)
        raise
