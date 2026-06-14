# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Local agent sandbox startup helpers.

This module owns the product-shaped contract for turning a copied local-agent
snapshot into a running endpoint target. The sandbox runner is split into a
generic backend that stages snapshots, writes state/config artifacts, executes
runtime launch plans, and cleans up, plus thin runtime descriptors such as the
OpenClaw/RAMPART descriptor.
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
from typing import Any, Callable
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
class LocalSandboxLaunchContext:
    """Resolved inputs shared by a Docker sandbox runtime descriptor."""

    manifest_path: Path
    manifest: dict[str, Any]
    output_dir: Path
    sandbox_root: Path
    endpoint_url: str
    endpoint: dict[str, Any]
    logs_dir: Path
    provider: str
    endpoint_port: int = 18081
    provider_route: str | None = None
    model_ref: str | None = None
    auth_proxy_port: int = 12435
    mock_openai_port: int = 18080
    auth_proxy_config: Path | None = None
    runtime_command_file: Path | None = None
    identity_staging_file: Path | None = None
    runner_root: Path = Path(".")
    rampart_root: Path = Path(".")
    docker_command: str = "docker.exe"
    sandbox_name: str = "local-agent"
    docker_timeout_seconds: int = 900
    dry_run: bool = False
    redact_paths: bool = True


@dataclass(frozen=True)
class LocalSandboxLaunchPlan:
    """Runtime-specific launch plan executed by the generic Docker backend."""

    steps: list[LocalSandboxStep]
    cleanup_commands: list[LocalSandboxCleanupCommand] = field(default_factory=list)
    plan_metadata: dict[str, Any] = field(default_factory=dict)
    prepare: Callable[[], None] | None = None

    @property
    def runner(self) -> str:
        return str(self.plan_metadata.get("runner", ""))

    @property
    def runtime_profile(self) -> str:
        return str(self.plan_metadata.get("runtime_profile", ""))


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


def _check_manifest_target(manifest: dict[str, Any], target: str) -> None:
    """Validate that a snapshot manifest is compatible with the selected descriptor.

    Discovery-path manifests use the canonical target id (e.g. 'openclaw'), so an
    exact match is required. Agent-config manifests record a free-form `id` as the
    target (e.g. 'openclaw-main-jp-desktop'); for those the user explicitly selects
    the descriptor via --target, and downstream content-validation of the staged
    snapshot is the real compatibility guard, so the cosmetic id is not required to
    match.
    """
    manifest_target = manifest.get("target")
    if manifest_target == target:
        return
    if manifest.get("source") == "agent_config":
        return
    raise ValueError(f"snapshot manifest target is {manifest_target!r}, not {target!r}")


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
        # A config with no explicit dest derives the runtime package to its
        # source basename ("openclaw/"). Accept it: the package is identified by
        # content (package.json + openclaw.mjs), not by the directory name.
        sandbox_root / "openclaw",
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


def _write_live_auth_proxy_config(path: Path, *, provider_route: str) -> None:
    if provider_route != "copilot":
        raise ValueError("live provider route must be one of: copilot")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
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
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _set_dotted_key(payload: dict[str, Any], dotted_key: str, value: Any) -> None:
    parts = [part for part in dotted_key.split(".") if part]
    if not parts:
        raise ValueError("model_routing key must not be empty")
    cursor: dict[str, Any] = payload
    for part in parts[:-1]:
        existing = cursor.get(part)
        if not isinstance(existing, dict):
            existing = {}
            cursor[part] = existing
        cursor = existing
    cursor[parts[-1]] = value


def _rewrite_model_routing_config(sandbox_root: Path, routing: RuntimeModelRouting, *, auth_proxy_port: int, provider_route: str) -> None:
    config_path = sandbox_root / routing.staged_config_file
    if not config_path.exists():
        raise ValueError(f"model_routing config file is missing from staged snapshot: {routing.staged_config_file}")
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"model_routing config file must be a YAML mapping: {routing.staged_config_file}")
    if routing.provider_key and routing.resolved_provider:
        _set_dotted_key(payload, routing.provider_key, routing.resolved_provider)
    if routing.base_url_key:
        _set_dotted_key(payload, routing.base_url_key, f"http://host.docker.internal:{auth_proxy_port}/{provider_route}")
    if routing.api_key_key:
        _set_dotted_key(payload, routing.api_key_key, "proxy-managed")
    if routing.api_mode_key and routing.resolved_api_mode:
        _set_dotted_key(payload, routing.api_mode_key, routing.resolved_api_mode)
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _write_generic_runtime_payload_files(
    *,
    output_dir: Path,
    runtime_command: tuple[str, ...] | None,
    identity_staging: tuple[dict[str, str], ...],
) -> tuple[Path, Path]:
    runtime_path = output_dir / "runtime-command.json"
    identity_path = output_dir / "identity-staging.json"
    runtime_path.write_text(
        json.dumps({"command": list(runtime_command or ())}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    identity_path.write_text(
        json.dumps({"entries": list(identity_staging)}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return runtime_path, identity_path


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


@dataclass(frozen=True)
class RuntimeModelRouting:
    """Model-provider rewrite to apply to the staged runtime config."""

    staged_config_file: str
    provider_key: str | None = None
    model_key: str | None = None
    api_mode_key: str | None = None
    base_url_key: str | None = None
    api_key_key: str | None = None
    resolved_provider: str | None = None
    resolved_api_mode: str | None = None


@dataclass(frozen=True)
class RuntimeLaunchConfig:
    """Data-driven local-agent runtime config for a sandbox launch."""

    id: str
    harness: str
    runtime_profile: str
    required_paths: tuple[str, ...] = ()
    launch_command: tuple[str, ...] | None = None
    runtime_command: tuple[str, ...] | None = None
    identity_staging: tuple[dict[str, str], ...] = ()
    endpoint_bridge_module: str | None = None
    endpoint_bridge_args: tuple[str, ...] = ()
    cleanup_labels: tuple[str, ...] = ("sandbox_stop", "sandbox_rm")
    endpoint_port: int = 18081
    auth_proxy_port: int = 12435
    mock_openai_port: int = 18080
    sandbox_name: str | None = None
    docker_command: str = "docker.exe"
    rampart_root: Path | str | None = None
    provider_route: str | None = None
    model_routing: RuntimeModelRouting | None = None


@dataclass(frozen=True)
class RampartRuntimeDescriptor:
    """Runtime-neutral inputs for a RAMPART Docker Sandbox launch."""

    runner_id: str
    runtime_profile: str
    required_paths: tuple[str, ...] = ()
    endpoint_bridge_module: str | None = None
    endpoint_bridge_args: tuple[str, ...] = ()
    launch_command: tuple[str, ...] | None = None
    runtime_command: tuple[str, ...] | None = None
    identity_staging: tuple[dict[str, str], ...] = ()
    launch_cwd: Path | None = None
    launch_log_name: str = "launch-rampart-sandbox.log"
    cleanup_labels: tuple[str, ...] = ("sandbox_stop", "sandbox_rm")
    endpoint_port: int = 18081
    auth_proxy_port: int = 12435
    mock_openai_port: int = 18080
    sandbox_name: str | None = None
    docker_command: str = "docker.exe"
    target: str | None = None
    rampart_root: Path | str | None = None
    provider_route: str | None = None
    model_routing: RuntimeModelRouting | None = None

    def _rampart_path(self) -> Path:
        if self.rampart_root is not None:
            return Path(self.rampart_root).expanduser().resolve()
        return _default_rampart_root()

    def validate(self, context: LocalSandboxLaunchContext) -> None:
        if not context.dry_run and self.launch_command is None:
            raise ValueError(
                "generic runtime live start requires a sandbox launcher command; "
                "agent launch.command is the runtime command inside the sandbox and must not be executed on the host"
            )
        missing = [relative for relative in self.required_paths if not (context.sandbox_root / relative).exists()]
        if missing:
            raise ValueError(f"snapshot is missing required runtime paths: {', '.join(missing)}")

    def build_plan(self, context: LocalSandboxLaunchContext) -> LocalSandboxLaunchPlan:
        return RampartDockerHarness(descriptor=self).build_plan(context)


def build_descriptor_from_runtime_config(config: RuntimeLaunchConfig) -> RampartRuntimeDescriptor:
    """Build a runtime descriptor from a data-only runtime config."""

    if config.harness != "rampart-docker":
        raise ValueError("runtime config harness must be one of: rampart-docker")
    return RampartRuntimeDescriptor(
        runner_id=config.id,
        runtime_profile=config.runtime_profile,
        required_paths=config.required_paths,
        endpoint_bridge_module=config.endpoint_bridge_module,
        endpoint_bridge_args=config.endpoint_bridge_args,
        launch_command=config.launch_command,
        runtime_command=config.runtime_command,
        identity_staging=config.identity_staging,
        cleanup_labels=config.cleanup_labels,
        endpoint_port=config.endpoint_port,
        auth_proxy_port=config.auth_proxy_port,
        mock_openai_port=config.mock_openai_port,
        sandbox_name=config.sandbox_name,
        docker_command=config.docker_command,
        target=config.id,
        rampart_root=config.rampart_root,
        provider_route=config.provider_route,
        model_routing=config.model_routing,
    )


def _default_snapshot_dest_for_source(source: Path) -> Path:
    return Path(source.name)


def _identity_staging_for_agent_config(agent_config: Any) -> tuple[dict[str, str], ...]:
    """Map staged snapshot roots back to their original absolute container paths.

    Agent configs use real-machine paths. For path-bound runtimes (Python venvs,
    editable installs, shebangs), the generic sandbox launcher needs to recreate
    those same absolute paths inside the container instead of inventing a
    sandbox-relative home. This plan is private runtime metadata; shareable
    manifests remain redacted.
    """

    entries: list[dict[str, str]] = []
    roots = [*getattr(agent_config, "roots", []), *getattr(agent_config, "external_dependencies", [])]
    for root in roots:
        source = root.source.expanduser().resolve()
        dest = Path(root.dest) if root.dest else _default_snapshot_dest_for_source(source)
        entries.append({"snapshot_path": dest.as_posix(), "container_path": str(source)})
    return tuple(entries)


def _staged_path_for_agent_source(agent_config: Any, source_path: Path) -> str | None:
    source = source_path.expanduser().resolve()
    roots = [*getattr(agent_config, "roots", []), *getattr(agent_config, "external_dependencies", [])]
    for root in roots:
        root_source = root.source.expanduser().resolve()
        try:
            rel = source.relative_to(root_source)
        except ValueError:
            continue
        dest = Path(root.dest) if root.dest else _default_snapshot_dest_for_source(root_source)
        return (dest / rel).as_posix()
    return None


def build_runtime_config_from_agent_config(agent_config: Any) -> RuntimeLaunchConfig:
    """Derive a RuntimeLaunchConfig from an agent's self-introspected config.

    This is the bridge that makes a generic run possible from the single file the
    introspection step emits: it maps the agent's declared launch command,
    endpoint, and model routing onto the RAMPART Docker harness the backend
    already drives. Runtime-specific knowledge comes from the self-report, not
    from hand-authored per-runtime code.
    """

    launch = getattr(agent_config, "launch", None)
    if launch is None or not getattr(launch, "command", None):
        raise ValueError("agent config requires a launch command to start a sandbox")

    endpoint = getattr(agent_config, "endpoint", None)
    endpoint_port = (endpoint.port if endpoint and endpoint.port else 18081)

    routing = getattr(agent_config, "model_routing", None)
    provider_route = getattr(routing, "resolved_provider", None) if routing else None
    runtime_model_routing = None
    if routing is not None and getattr(routing, "config_file", None) is not None:
        staged_config_file = _staged_path_for_agent_source(agent_config, routing.config_file)
        if staged_config_file is not None:
            runtime_model_routing = RuntimeModelRouting(
                staged_config_file=staged_config_file,
                provider_key=getattr(routing, "provider_key", None),
                model_key=getattr(routing, "model_key", None),
                api_mode_key=getattr(routing, "api_mode_key", None),
                base_url_key=getattr(routing, "base_url_key", None),
                api_key_key=getattr(routing, "api_key_key", None),
                resolved_provider=getattr(routing, "resolved_provider", None),
                resolved_api_mode=getattr(routing, "resolved_api_mode", None),
            )

    return RuntimeLaunchConfig(
        id=agent_config.id,
        harness="rampart-docker",
        runtime_profile=agent_config.id,
        launch_command=None,
        runtime_command=tuple(launch.command),
        identity_staging=_identity_staging_for_agent_config(agent_config),
        endpoint_port=int(endpoint_port),
        provider_route=provider_route,
        model_routing=runtime_model_routing,
        rampart_root=_default_rampart_root(),
    )


def _tuple_from_sequence(value: Any, *, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list | tuple) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"runtime config field {field_name!r} must be a list of strings")
    return tuple(value)


def load_runtime_config(path: str | Path) -> RuntimeLaunchConfig:
    """Load a data-driven runtime config from YAML."""

    config_path = Path(path).expanduser().resolve()
    try:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"could not read runtime config: {config_path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("runtime config must be a YAML mapping")
    required = ["id", "harness", "runtime_profile"]
    missing = [field for field in required if not isinstance(payload.get(field), str) or not payload.get(field)]
    if missing:
        raise ValueError(f"runtime config is missing required fields: {', '.join(missing)}")
    return RuntimeLaunchConfig(
        id=str(payload["id"]),
        harness=str(payload["harness"]),
        runtime_profile=str(payload["runtime_profile"]),
        required_paths=_tuple_from_sequence(payload.get("required_paths"), field_name="required_paths"),
        launch_command=_tuple_from_sequence(payload.get("launch_command"), field_name="launch_command") or None,
        endpoint_bridge_module=payload.get("endpoint_bridge_module") if isinstance(payload.get("endpoint_bridge_module"), str) else None,
        endpoint_bridge_args=_tuple_from_sequence(payload.get("endpoint_bridge_args"), field_name="endpoint_bridge_args"),
        cleanup_labels=_tuple_from_sequence(payload.get("cleanup_labels"), field_name="cleanup_labels") or ("sandbox_stop", "sandbox_rm"),
        endpoint_port=int(payload.get("endpoint_port", 18081)),
        auth_proxy_port=int(payload.get("auth_proxy_port", 12435)),
        mock_openai_port=int(payload.get("mock_openai_port", 18080)),
        sandbox_name=payload.get("sandbox_name") if isinstance(payload.get("sandbox_name"), str) else None,
        docker_command=str(payload.get("docker_command", "docker.exe")),
        rampart_root=payload.get("rampart_root") if isinstance(payload.get("rampart_root"), str) else None,
        provider_route=payload.get("provider_route") if isinstance(payload.get("provider_route"), str) else None,
    )


def _launch_template_values(context: LocalSandboxLaunchContext) -> dict[str, str]:
    return {
        "output_dir": str(context.output_dir),
        "sandbox_root": str(context.sandbox_root),
        "endpoint_url": context.endpoint_url,
        "endpoint_port": str(context.endpoint_port),
        "auth_proxy_port": str(context.auth_proxy_port),
        "mock_openai_port": str(context.mock_openai_port),
        "runtime_command_file": str(context.runtime_command_file or context.output_dir / "runtime-command.json"),
        "identity_staging_file": str(context.identity_staging_file or context.output_dir / "identity-staging.json"),
        "sandbox_name": context.sandbox_name,
        "provider": context.provider,
        "provider_route": context.provider_route or "",
        "model_ref": context.model_ref or "",
    }


def _expand_launch_template_value(value: str, context: LocalSandboxLaunchContext) -> str:
    rendered = value
    for key, replacement in _launch_template_values(context).items():
        rendered = rendered.replace("{" + key + "}", replacement)
    return rendered


def _expand_launch_templates(values: tuple[str, ...] | None, context: LocalSandboxLaunchContext) -> tuple[str, ...] | None:
    if values is None:
        return None
    return tuple(_expand_launch_template_value(value, context) for value in values)


def _identity_staging_json(entries: tuple[dict[str, str], ...], *, redact_paths: bool) -> list[dict[str, str]]:
    rendered: list[dict[str, str]] = []
    for entry in entries:
        rendered.append(
            {
                "snapshot_path": entry["snapshot_path"],
                "container_path": _redact_local_string(entry["container_path"], redact_paths=redact_paths),
            }
        )
    return rendered


class RampartDockerHarness:
    """Reusable RAMPART Docker Sandbox launch/proxy/bridge plan builder."""

    name = "rampart-docker"

    def __init__(self, *, descriptor: RampartRuntimeDescriptor) -> None:
        self.descriptor = descriptor

    def build_plan(self, context: LocalSandboxLaunchContext) -> LocalSandboxLaunchPlan:
        launch_command = _expand_launch_templates(self.descriptor.launch_command, context)
        endpoint_bridge_args = _expand_launch_templates(self.descriptor.endpoint_bridge_args, context) or ()
        steps: list[LocalSandboxStep] = [
            LocalSandboxStep(
                name="preflight",
                command=[sys.executable, "-c", "import pathlib, sys; sys.exit(0)"],
                cwd=context.output_dir,
                timeout_seconds=120,
            )
        ]
        if context.provider == "mock":
            steps.append(
                LocalSandboxStep(
                    name="start_mock_openai",
                    command=[sys.executable, "-m", "assert_ai.local_sandbox_runtime.mock_openai_server", "--port", str(context.mock_openai_port)],
                    cwd=context.output_dir,
                    kind="service",
                    health_url=f"http://127.0.0.1:{context.mock_openai_port}/health",
                    log_name="mock-openai.log",
                )
            )
        steps.extend(
            [
                LocalSandboxStep(
                    name="start_auth_proxy",
                    command=[
                        _rampart_python(context.rampart_root),
                        str(context.rampart_root / "scripts" / "run_auth_proxy.py"),
                        "serve",
                        "--config",
                        str(context.auth_proxy_config or context.output_dir / "auth-proxy.json"),
                        "--port",
                        str(context.auth_proxy_port),
                        "--request-log",
                        str(context.output_dir / "auth-proxy-requests.jsonl"),
                        "-v",
                    ],
                    cwd=context.rampart_root,
                    kind="service",
                    health_url=f"http://127.0.0.1:{context.auth_proxy_port}/health",
                    log_name="auth-proxy.log",
                ),
                LocalSandboxStep(
                    name="launch_rampart_sandbox",
                    command=list(launch_command or (
                        "powershell.exe",
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-Command",
                        f"Write-Host 'launch {context.sandbox_name}'",
                    )),
                    cwd=self.descriptor.launch_cwd or context.output_dir,
                    timeout_seconds=context.docker_timeout_seconds,
                    log_name=self.descriptor.launch_log_name,
                ),
            ]
        )
        if self.descriptor.endpoint_bridge_module:
            steps.append(
                LocalSandboxStep(
                    name="start_endpoint_bridge",
                    command=[
                        _rampart_python(context.rampart_root),
                        "-m",
                        self.descriptor.endpoint_bridge_module,
                        *endpoint_bridge_args,
                    ],
                    cwd=context.output_dir,
                    kind="service",
                    health_url=f"{context.endpoint_url}/health",
                    log_name="endpoint-bridge.log",
                )
            )
        cleanup_commands = [
            LocalSandboxCleanupCommand(label, [context.docker_command, "sandbox", action, context.sandbox_name])
            for label, action in zip(self.descriptor.cleanup_labels, ("stop", "rm"), strict=False)
        ]
        # The auth proxy needs a routes file. OpenClaw pre-supplies one via
        # context.auth_proxy_config; the generic path must write it here so the
        # proxy starts with real routes instead of exiting with "no routes".
        routes_path = context.auth_proxy_config or (context.output_dir / "auth-proxy.json")
        prepare = None
        if context.auth_proxy_config is None or self.descriptor.model_routing is not None:
            provider = context.provider
            mock_port = context.mock_openai_port
            route = context.provider_route or "copilot"
            model_routing = self.descriptor.model_routing

            def _prepare_runtime() -> None:
                if context.auth_proxy_config is None:
                    if provider == "mock":
                        _write_mock_auth_proxy_config(routes_path, mock_openai_port=mock_port)
                    else:
                        _write_live_auth_proxy_config(routes_path, provider_route=route)
                if model_routing is not None:
                    _rewrite_model_routing_config(context.sandbox_root, model_routing, auth_proxy_port=context.auth_proxy_port, provider_route=route)

            prepare = _prepare_runtime
        return LocalSandboxLaunchPlan(
            steps=steps,
            cleanup_commands=cleanup_commands,
            plan_metadata={
                "harness": self.name,
                "runner": self.descriptor.runner_id,
                "runtime_profile": self.descriptor.runtime_profile,
                "provider": context.provider,
                "provider_route": context.provider_route,
                "model_ref": context.model_ref,
                "runtime_command": _redact_command(list(self.descriptor.runtime_command), redact_paths=context.redact_paths)
                if self.descriptor.runtime_command
                else None,
                "runtime_command_file": _path_value(context.runtime_command_file, redact_paths=context.redact_paths),
                "identity_staging": _identity_staging_json(self.descriptor.identity_staging, redact_paths=context.redact_paths),
                "identity_staging_file": _path_value(context.identity_staging_file, redact_paths=context.redact_paths),
                "sandbox_name": context.sandbox_name,
                "auth_proxy_config": _path_value(routes_path, redact_paths=context.redact_paths),
            },
            prepare=prepare,
        )


class DockerSandboxBackend:
    """Generic Docker-style sandbox backend that executes a runtime descriptor."""

    def __init__(self, *, descriptor: Any) -> None:
        self.descriptor = descriptor

    def start(
        self,
        *,
        snapshot_manifest_path: str | Path,
        target: str,
        output_dir: str | Path,
        endpoint_url: str,
        provider: str,
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
        """Stage a snapshot and run the descriptor's launch plan."""

        descriptor_target = getattr(self.descriptor, "target", None)
        if descriptor_target is not None and descriptor_target != target:
            raise ValueError(f"runtime descriptor target is {descriptor_target!r}, not {target!r}")

        run_step_fn = run_step or _default_run_step
        start_step_fn = start_step or _default_start_step
        stop_process_fn = stop_process or (lambda pid, timeout_seconds: _terminate_process_group_status(pid, timeout_seconds=timeout_seconds))
        cleanup_step_fn = cleanup_step or _default_cleanup_step

        manifest_path = Path(snapshot_manifest_path).expanduser().resolve()
        output = Path(output_dir).expanduser().resolve()
        manifest = _load_snapshot_manifest(manifest_path)
        _check_manifest_target(manifest, target)

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
        logs_dir = output / "logs"
        runtime_command_file: Path | None = None
        identity_staging_file: Path | None = None
        descriptor_runtime_command = getattr(self.descriptor, "runtime_command", None)
        descriptor_identity_staging = getattr(self.descriptor, "identity_staging", ())
        if descriptor_runtime_command or descriptor_identity_staging:
            runtime_command_file, identity_staging_file = _write_generic_runtime_payload_files(
                output_dir=output,
                runtime_command=descriptor_runtime_command,
                identity_staging=descriptor_identity_staging,
            )
        context = LocalSandboxLaunchContext(
            manifest_path=manifest_path,
            manifest=manifest,
            output_dir=output,
            sandbox_root=sandbox_root,
            endpoint_url=endpoint_url,
            endpoint_port=int(getattr(self.descriptor, "endpoint_port", 18081)),
            endpoint=endpoint,
            logs_dir=logs_dir,
            provider=provider,
            provider_route=getattr(self.descriptor, "provider_route", None),
            model_ref=getattr(self.descriptor, "model_ref", None),
            auth_proxy_port=int(getattr(self.descriptor, "auth_proxy_port", 12435)),
            mock_openai_port=int(getattr(self.descriptor, "mock_openai_port", 18080)),
            auth_proxy_config=Path(getattr(self.descriptor, "auth_proxy_config")).expanduser().resolve() if getattr(self.descriptor, "auth_proxy_config", None) else None,
            runtime_command_file=runtime_command_file,
            identity_staging_file=identity_staging_file,
            runner_root=getattr(self.descriptor, "_runner_path", lambda: Path("."))(),
            rampart_root=getattr(self.descriptor, "_rampart_path", lambda: Path("."))(),
            docker_command=str(getattr(self.descriptor, "docker_command", "docker.exe")),
            sandbox_name=str(getattr(self.descriptor, "sandbox_name", None) or target),
            docker_timeout_seconds=int(getattr(self.descriptor, "docker_timeout_seconds", 900)),
            dry_run=dry_run,
            redact_paths=redact_paths,
        )
        validate = getattr(self.descriptor, "validate", None)
        if validate is not None:
            validate(context)
        plan = self.descriptor.build_plan(context)
        runner_name = str(getattr(self.descriptor, "runner_name", "runtime"))
        plan_steps = [_local_sandbox_step_json(step, redact_paths=redact_paths) for step in plan.steps]
        cleanup_json = [_local_cleanup_json(command, redact_paths=redact_paths) for command in plan.cleanup_commands]
        plan_metadata = dict(plan.plan_metadata)
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
                "runner": runner_name,
                **plan_metadata,
                "steps": plan_steps,
            },
            "processes": [],
            "cleanup": {
                "required": bool(plan.cleanup_commands) and not dry_run,
                "commands": cleanup_json,
            },
            "safety": {
                "live_home_mount": False,
                "snapshot_staged": True,
                "endpoint_local_dev": True,
                "external_services_proxied_or_mocked": True,
            },
        }
        sandbox_name = plan_metadata.get("sandbox_name")
        if sandbox_name is not None:
            state["cleanup"]["sandbox_name"] = sandbox_name
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
            if plan.prepare is not None:
                plan.prepare()
            for step in plan.steps:
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
            for command in plan.cleanup_commands:
                cleanup_step_fn(command.command, cleanup_dir / f"{command.name}.log", command.timeout_seconds)
            raise


@dataclass(frozen=True)
class OpenClawDockerLaunchDescriptor:
    """OpenClaw/RAMPART launch descriptor for the generic Docker backend."""

    runner_root: str | Path | None = None
    rampart_root: str | Path | None = None
    sandbox_name: str = "oc-local-agent"
    provider: str = "mock"
    provider_route: str = "copilot"
    model_ref: str = "openai/mock-model=Mock Model"
    endpoint_port: int = 18081
    auth_proxy_port: int = 12435
    mock_openai_port: int = 18080
    docker_command: str = "docker.exe"
    skip_build: bool = False
    auth_proxy_config: str | Path | None = None
    target: str = "openclaw"
    runner_name: str = "openclaw-docker-sandbox"

    def _runner_path(self) -> Path:
        return Path(self.runner_root).expanduser().resolve() if self.runner_root else _default_runner_root()

    def _rampart_path(self) -> Path:
        return Path(self.rampart_root).expanduser().resolve() if self.rampart_root else _default_rampart_root()

    def validate(self, context: LocalSandboxLaunchContext) -> None:
        if self.provider not in {"mock", "live"}:
            raise ValueError("provider must be one of: mock, live")
        if self.provider == "live" and self.provider_route != "copilot":
            raise ValueError("live provider route must be one of: copilot")
        _find_staged_workspace(context.sandbox_root)
        _find_staged_openclaw_runtime(context.sandbox_root)
        runner_path = self._runner_path()
        rampart_path = self._rampart_path()
        helpers = [runner_path / "start_openclaw_sandbox.ps1", runner_path / "openclaw_endpoint_bridge.py"]
        if self.provider == "mock":
            helpers.append(runner_path / "mock_openai_server.py")
        for helper in helpers:
            if not helper.exists():
                raise ValueError(f"local sandbox helper is missing: {helper}")
        if not (rampart_path / "scripts" / "openclaw-sandbox.ps1").exists():
            raise ValueError(f"OpenClaw Docker sandbox launcher not found under: {rampart_path}")
        if not (rampart_path / "scripts" / "run_auth_proxy.py").exists():
            raise ValueError(f"OpenClaw auth proxy runner not found under: {rampart_path}")

    def build_plan(self, context: LocalSandboxLaunchContext) -> LocalSandboxLaunchPlan:
        workspace = _find_staged_workspace(context.sandbox_root)
        runtime_path = _find_staged_openclaw_runtime(context.sandbox_root)
        _ensure_source_bundle(workspace)
        output = context.output_dir
        runner_path = self._runner_path()
        rampart_path = self._rampart_path()
        runtime_archive = output / "runtime" / "openclaw-runtime.tar.gz"
        mock_config_path = output / "mock-auth-proxy.json"
        live_config_path = output / "live-auth-proxy.json"
        resolved_auth_proxy_config = Path(self.auth_proxy_config).expanduser().resolve() if self.auth_proxy_config else (mock_config_path if self.provider == "mock" else live_config_path)
        if self.provider == "mock" and not self.auth_proxy_config:
            _write_mock_auth_proxy_config(mock_config_path, mock_openai_port=self.mock_openai_port)
        if self.provider == "live" and not self.auth_proxy_config:
            _write_live_auth_proxy_config(live_config_path, provider_route=self.provider_route)

        model_json = _models_json(self.model_ref)
        launcher = runner_path / "start_openclaw_sandbox.ps1"
        endpoint_bridge = runner_path / "openclaw_endpoint_bridge.py"
        openclaw_launch_command = (
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(launcher),
            "-RampartOpenClawRoot",
            str(rampart_path),
            "-SandboxName",
            self.sandbox_name,
            "-Models",
            model_json,
            "-AuthProxyPort",
            str(self.auth_proxy_port),
            "-RuntimeArchive",
            str(runtime_archive),
            *(["-SkipBuild"] if self.skip_build else []),
        )
        harness_descriptor = RampartRuntimeDescriptor(
            runner_id=self.runner_name,
            runtime_profile="openclaw",
            required_paths=(".openclaw/workspace", "runtime/openclaw-package"),
            endpoint_bridge_module="assert_ai.local_sandbox_runtime.openclaw_endpoint_bridge",
            launch_command=openclaw_launch_command,
            launch_cwd=runner_path,
        )
        harness_context = LocalSandboxLaunchContext(
            manifest_path=context.manifest_path,
            manifest=context.manifest,
            output_dir=context.output_dir,
            sandbox_root=context.sandbox_root,
            endpoint_url=context.endpoint_url,
            endpoint_port=self.endpoint_port,
            endpoint=context.endpoint,
            logs_dir=context.logs_dir,
            provider=self.provider,
            provider_route=self.provider_route if self.provider == "live" else None,
            model_ref=self.model_ref,
            auth_proxy_port=self.auth_proxy_port,
            mock_openai_port=self.mock_openai_port,
            auth_proxy_config=resolved_auth_proxy_config,
            runner_root=runner_path,
            rampart_root=rampart_path,
            docker_command=self.docker_command,
            sandbox_name=self.sandbox_name,
            docker_timeout_seconds=900,
            dry_run=context.dry_run,
            redact_paths=context.redact_paths,
        )
        common_plan = RampartDockerHarness(descriptor=harness_descriptor).build_plan(harness_context)
        steps = list(common_plan.steps)
        insert_at = 1
        steps.insert(
            insert_at,
            LocalSandboxStep(
                name="prepare_openclaw_runtime_archive",
                command=[sys.executable, "-c", "import pathlib, sys; sys.exit(0)"],
                cwd=output,
                timeout_seconds=300,
            ),
        )
        for index, step in enumerate(steps):
            if step.name == "start_endpoint_bridge":
                steps[index] = LocalSandboxStep(
                    name="start_endpoint_bridge",
                    command=[
                        _rampart_python(rampart_path),
                        str(endpoint_bridge),
                        "--sandbox-name",
                        self.sandbox_name,
                        "--workspace",
                        str(workspace),
                        "--rampart-root",
                        str(rampart_path),
                        "--port",
                        str(self.endpoint_port),
                        "--docker-command",
                        self.docker_command,
                    ],
                    cwd=runner_path,
                    kind="service",
                    health_url=f"{context.endpoint_url}/health",
                    log_name="endpoint-bridge.log",
                    env={"ASSERT_DOCKER_COMMAND": self.docker_command, "RAMPART_OPENCLAW_ROOT": str(rampart_path)},
                )
        return LocalSandboxLaunchPlan(
            steps=steps,
            cleanup_commands=common_plan.cleanup_commands,
            plan_metadata={
                **common_plan.plan_metadata,
                "runtime_profile": "openclaw",
                "endpoint_port": self.endpoint_port,
                "auth_proxy_port": self.auth_proxy_port,
                "mock_openai_port": self.mock_openai_port if self.provider == "mock" else None,
                "runtime_archive": _path_value(runtime_archive, redact_paths=context.redact_paths),
                "workspace": _path_value(workspace, redact_paths=context.redact_paths),
                "source_bundle": _path_value(workspace / "source-bundle.json", redact_paths=context.redact_paths),
                "workspace_fidelity": {
                    "active_workspace": "/home/agent/.openclaw/workspace",
                    "verified_by": "endpoint_bridge_sentinel_hashes",
                },
                "auth_proxy_config": _path_value(resolved_auth_proxy_config, redact_paths=context.redact_paths),
            },
            prepare=lambda: _write_runtime_archive(runtime_path, runtime_archive),
        )


def start_openclaw_docker_sandbox(
    *,
    snapshot_manifest_path: str | Path,
    target: str,
    output_dir: str | Path,
    runner_root: str | Path | None = None,
    rampart_root: str | Path | None = None,
    sandbox_name: str = "oc-local-agent",
    provider: str = "mock",
    provider_route: str = "copilot",
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
    """Start OpenClaw through the generic Docker backend using its launch descriptor."""

    descriptor = OpenClawDockerLaunchDescriptor(
        runner_root=runner_root,
        rampart_root=rampart_root,
        sandbox_name=sandbox_name,
        provider=provider,
        provider_route=provider_route,
        model_ref=model_ref,
        endpoint_port=endpoint_port,
        auth_proxy_port=auth_proxy_port,
        mock_openai_port=mock_openai_port,
        docker_command=docker_command,
        skip_build=skip_build,
        auth_proxy_config=auth_proxy_config,
    )
    backend = DockerSandboxBackend(descriptor=descriptor)
    return backend.start(
        snapshot_manifest_path=snapshot_manifest_path,
        target=target,
        output_dir=output_dir,
        endpoint_url=f"http://127.0.0.1:{endpoint_port}",
        provider=provider,
        protocol=protocol,
        model=model,
        api_key_env=api_key_env,
        stream=stream,
        redact_paths=redact_paths,
        dry_run=dry_run,
        run_step=run_step,
        start_step=start_step,
        stop_process=stop_process,
        cleanup_step=cleanup_step,
    )

_OPENCLAW_CONFIGURED_WORKSPACE_SMOKE_MESSAGE = (
    "What should you call the user, and is this a fresh first-run workspace or an already configured workspace? "
    "Answer in one short paragraph."
)
_OPENCLAW_FIRST_RUN_SIGNALS = (
    "came online",
    "fresh workspace",
    "who am i",
    "who are you",
    "what should i call you",
    "what should you call me",
)


def _first_run_workspace_signals(response_text: str) -> list[str]:
    lowered = response_text.casefold()
    return [signal for signal in _OPENCLAW_FIRST_RUN_SIGNALS if signal in lowered]


def smoke_local_sandbox(
    state_path: str | Path,
    *,
    message: str | None = None,
    timeout_seconds: float = 240.0,
    configured_workspace_check: bool | None = None,
) -> dict[str, Any]:
    """Send a request to a started sandbox endpoint and optionally check configured-workspace fidelity."""

    state_file = Path(state_path).expanduser().resolve()
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read sandbox state: {state_file}") from exc
    target = str(state.get("target") or "")
    check_configured_workspace = (
        configured_workspace_check
        if configured_workspace_check is not None
        else target == "openclaw" and message is None
    )
    smoke_message = message or (
        _OPENCLAW_CONFIGURED_WORKSPACE_SMOKE_MESSAGE
        if check_configured_workspace
        else "Reply exactly: ok"
    )
    endpoint = state.get("endpoint")
    if not isinstance(endpoint, dict) or not isinstance(endpoint.get("url"), str):
        raise ValueError("sandbox state must include endpoint.url")
    endpoint_url = endpoint["url"]
    validate_endpoint_url(endpoint_url, allow_localhost=True)
    payload = json.dumps({"message": smoke_message, "history": []}).encode("utf-8")
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
    status = "ok" if isinstance(final_response, str) and final_response else "empty_response"
    result = {
        "schema_version": 1,
        "target": state.get("target"),
        "agent_endpoint": endpoint_url,
        "status": status,
        "response": final_response,
        "events": response_json.get("events", []),
        "metadata": response_json.get("metadata", {}),
    }
    if check_configured_workspace:
        signals = _first_run_workspace_signals(final_response if isinstance(final_response, str) else "")
        check_status = "failed" if signals else "ok"
        result["configured_workspace_check"] = {
            "status": check_status,
            "failure_signals": signals,
        }
        if signals:
            result["status"] = "failed"
    return result


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


DEFAULT_SANDBOXES_DIR = Path("artifacts") / "local-agents" / "sandboxes"


def _sandbox_state_is_live(state_path: Path) -> bool:
    """Return True if a sandbox state file marks a running sandbox with a live process."""
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(state, dict) or state.get("status") != "running":
        return False
    processes = state.get("processes")
    if not isinstance(processes, list):
        return False
    for record in processes:
        if not isinstance(record, dict):
            continue
        pid = record.get("pid")
        if isinstance(pid, int) and not _pid_has_exited(pid):
            return True
    return False


def find_running_sandbox_state(*, sandboxes_dir: str | Path | None = None) -> Path:
    """Find the single running local-agent sandbox state file.

    Scans ``sandboxes_dir/*/sandbox_state.json`` for states that are marked
    ``running`` AND have at least one process whose pid is still alive. A
    ``status: running`` state whose processes have all exited (a stale state) is
    treated as not running. Raises ``ValueError`` if zero or more than one live
    sandbox is found, so callers can prompt for an explicit ``--state``.
    """
    base = Path(sandboxes_dir if sandboxes_dir is not None else DEFAULT_SANDBOXES_DIR).expanduser()
    live: list[Path] = []
    if base.is_dir():
        for state_path in sorted(base.glob("*/sandbox_state.json")):
            if _sandbox_state_is_live(state_path):
                live.append(state_path)
    if not live:
        raise ValueError(
            f"no running local-agent sandbox found under {base}; pass --state explicitly "
            "or start one with `assert-ai local sandbox start`"
        )
    if len(live) > 1:
        names = ", ".join(str(path.parent.name) for path in live)
        raise ValueError(
            f"multiple running local-agent sandboxes found ({names}); pass --state to choose one"
        )
    return live[0]


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
    _check_manifest_target(manifest, target)

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
