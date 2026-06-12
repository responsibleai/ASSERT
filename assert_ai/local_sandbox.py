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

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import time
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

import yaml

from assert_ai.core.io import write_json
from assert_ai.core.security import validate_endpoint_url


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


def start_openclaw_docker_sandbox(
    *,
    snapshot_manifest_path: str | Path,
    target: str,
    output_dir: str | Path,
    rampart_root: str | Path | None = None,
    sandbox_name: str = "oc-local-agent",
    provider: str = "mock",
    model_ref: str = "openai/mock-model=Mock Model",
    endpoint_port: int = 18081,
    protocol: str = "assert",
    model: str | None = None,
    api_key_env: str | None = None,
    stream: bool = False,
    redact_paths: bool = True,
    dry_run: bool = False,
) -> LocalSandboxStartResult:
    """Prepare or start an OpenClaw Docker sandbox using the product state contract."""

    if target != "openclaw":
        raise ValueError("the docker backend currently supports target openclaw only")
    if provider not in {"mock", "live"}:
        raise ValueError("provider must be one of: mock, live")
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
    state_path = output / "sandbox_state.json"
    config_path = output / "endpoint_target.yaml"
    rampart_path = Path(rampart_root).expanduser().resolve() if rampart_root else None
    state = {
        "schema_version": 1,
        "target": target,
        "backend": "docker",
        "status": "planned" if dry_run else "not_started",
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
            "runner": "openclaw-docker-sandbox",
            "runner_root": _path_value(rampart_path, redact_paths=redact_paths),
            "steps": _openclaw_docker_plan_steps(provider=provider),
        },
        "processes": [],
        "cleanup": {
            "sandbox_name": sandbox_name,
            "required": not dry_run,
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
    if not dry_run:
        raise ValueError("docker backend execution is not wired yet; rerun with --dry-run")
    return LocalSandboxStartResult(
        state_path=state_path,
        config_path=config_path,
        sandbox_root=sandbox_root,
        endpoint_url=endpoint_url,
        process=None,
    )


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

    state["status"] = "stopped"
    state["stopped_at"] = datetime.now(timezone.utc).isoformat()
    state["stopped_processes"] = stopped
    write_json(state_file, state)
    return {
        "schema_version": 1,
        "target": state.get("target"),
        "status": "stopped",
        "state": str(state_file),
        "processes": stopped,
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
