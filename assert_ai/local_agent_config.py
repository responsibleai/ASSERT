# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Agent runtime-config: an agent's declarative self-report of what to copy.

A runtime (OpenClaw, Hermes, Codex, ...) emits a small YAML describing where its
files live and how to launch a configured clone. This module parses that file
into typed objects. Two firm design decisions:

* **Paths are real-machine / source-relative.** The agent declares where its
  files actually live on disk (``~/.openclaw/workspace``). ASSERT owns the
  translation into sandbox-findable locations; the agent never reasons about the
  post-copy layout.
* **Everything optional except ``id``.** The schema is a superset. The agent
  fills what it has and omits the rest.

The companion design doc lives outside this customer-preview repo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ConfigRoot:
    """A single directory (or file) the agent wants copied into the snapshot."""

    source: Path
    dest: str | None = None
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    required: bool = False
    kind: str | None = None


@dataclass(frozen=True)
class LaunchSpec:
    """How to start the configured runtime inside the sandbox."""

    command: tuple[str, ...]
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class EndpointSpec:
    """The HTTP chat endpoint the running runtime exposes."""

    url: str
    protocol: str = "openai_chat"
    model: str | None = None

    @property
    def port(self) -> int | None:
        """Parse the loopback port from the endpoint URL, if present."""

        from urllib.parse import urlparse

        try:
            return urlparse(self.url).port
        except ValueError:
            return None


@dataclass(frozen=True)
class ModelRoutingSpec:
    """How to redirect the runtime's model calls through the host auth proxy.

    Two worlds: an env-var runtime needs nothing here, but a config-file runtime
    (like Hermes) needs its provider config rewritten to point ``base_url`` at the
    proxy. The agent self-reports the file + dotted keys so no per-runtime code is
    hand-authored.
    """

    config_file: Path | None = None
    provider_key: str | None = None
    model_key: str | None = None
    api_mode_key: str | None = None
    base_url_key: str | None = None
    api_key_key: str | None = None
    credential_file: Path | None = None
    resolved_provider: str | None = None
    resolved_base_url: str | None = None
    resolved_api_mode: str | None = None


@dataclass(frozen=True)
class SmokeProbeSpec:
    """A question whose answer reveals whether the clone loaded real config."""

    prompt: str


@dataclass(frozen=True)
class AgentRuntimeConfig:
    """Parsed agent runtime-config."""

    id: str
    roots: list[ConfigRoot]
    display_name: str | None = None
    schema_version: int = 1
    exclude: list[str] = field(default_factory=list)
    instruction_files: list[str] = field(default_factory=list)
    external_dependencies: list[ConfigRoot] = field(default_factory=list)
    launch: LaunchSpec | None = None
    endpoint: EndpointSpec | None = None
    model_routing: ModelRoutingSpec | None = None
    smoke_probe: SmokeProbeSpec | None = None


def _as_str_list(value: Any, *, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} must be a list of strings")
    return list(value)


def _validate_dest(dest: str) -> str:
    dest_path = Path(dest)
    if not dest or dest_path.is_absolute() or ".." in dest_path.parts:
        raise ValueError("root dest must be relative and stay inside the snapshot")
    return dest


def _parse_root(raw: Any, *, default_kind: str | None = None) -> ConfigRoot:
    if not isinstance(raw, dict):
        raise ValueError("each root must be a mapping")
    source_raw = raw.get("source")
    if not isinstance(source_raw, str) or not source_raw:
        raise ValueError("each root requires a source path")
    source = Path(source_raw).expanduser()
    dest_raw = raw.get("dest")
    dest = _validate_dest(dest_raw) if isinstance(dest_raw, str) and dest_raw else None
    kind = str(raw["kind"]) if raw.get("kind") is not None else default_kind
    return ConfigRoot(
        source=source,
        dest=dest,
        include=_as_str_list(raw.get("include"), field_name="root include"),
        exclude=_as_str_list(raw.get("exclude"), field_name="root exclude"),
        required=bool(raw.get("required", False)),
        kind=kind,
    )


def _parse_launch(raw: Any) -> LaunchSpec | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("launch must be a mapping")
    command = _as_str_list(raw.get("command"), field_name="launch command")
    if not command:
        raise ValueError("launch requires a non-empty command list")
    env_raw = raw.get("env") or {}
    if not isinstance(env_raw, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env_raw.items()):
        raise ValueError("launch env must be a mapping of strings")
    return LaunchSpec(command=tuple(command), env=dict(env_raw))


def _parse_endpoint(raw: Any) -> EndpointSpec | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("endpoint must be a mapping")
    url = raw.get("url")
    if not isinstance(url, str) or not url:
        raise ValueError("endpoint requires a string url")
    protocol = raw.get("protocol", "openai_chat")
    model = raw.get("model")
    return EndpointSpec(
        url=url,
        protocol=str(protocol) if protocol else "openai_chat",
        model=str(model) if model is not None else None,
    )


def _opt_path(value: Any) -> Path | None:
    return Path(str(value)).expanduser() if isinstance(value, str) and value else None


def _opt_str(value: Any) -> str | None:
    return str(value) if isinstance(value, str) and value else None


def _parse_model_routing(raw: Any) -> ModelRoutingSpec | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("model_routing must be a mapping")
    return ModelRoutingSpec(
        config_file=_opt_path(raw.get("config_file")),
        provider_key=_opt_str(raw.get("provider_key")),
        model_key=_opt_str(raw.get("model_key")),
        api_mode_key=_opt_str(raw.get("api_mode_key")),
        base_url_key=_opt_str(raw.get("base_url_key")),
        api_key_key=_opt_str(raw.get("api_key_key")),
        credential_file=_opt_path(raw.get("credential_file")),
        resolved_provider=_opt_str(raw.get("resolved_provider")),
        resolved_base_url=_opt_str(raw.get("resolved_base_url")),
        resolved_api_mode=_opt_str(raw.get("resolved_api_mode")),
    )


def _parse_smoke_probe(raw: Any) -> SmokeProbeSpec | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("smoke_probe must be a mapping")
    prompt = raw.get("prompt")
    if not isinstance(prompt, str) or not prompt:
        raise ValueError("smoke_probe requires a string prompt")
    return SmokeProbeSpec(prompt=prompt)


def load_agent_config(path: str | Path) -> AgentRuntimeConfig:
    """Load and validate an agent runtime-config YAML file."""

    config_path = Path(path).expanduser().resolve()
    try:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"could not read agent config: {config_path}") from exc

    if not isinstance(payload, dict):
        raise ValueError("agent config must be a YAML mapping")

    agent_id = payload.get("id")
    if not isinstance(agent_id, str) or not agent_id:
        raise ValueError("agent config requires a string id")

    roots_raw = payload.get("roots")
    if roots_raw is None:
        roots_raw = []
    if not isinstance(roots_raw, list):
        raise ValueError("roots must be a list")
    roots = [_parse_root(item) for item in roots_raw]

    ext_raw = payload.get("external_dependencies")
    if ext_raw is None:
        ext_raw = []
    if not isinstance(ext_raw, list):
        raise ValueError("external_dependencies must be a list")
    external_dependencies = [_parse_root(item, default_kind="external_dependency") for item in ext_raw]

    display_name = payload.get("display_name")
    schema_version = payload.get("schema_version", 1)

    return AgentRuntimeConfig(
        id=agent_id,
        roots=roots,
        display_name=str(display_name) if display_name is not None else None,
        schema_version=int(schema_version) if isinstance(schema_version, int) else 1,
        exclude=_as_str_list(payload.get("exclude"), field_name="exclude"),
        instruction_files=_as_str_list(payload.get("instruction_files"), field_name="instruction_files"),
        external_dependencies=external_dependencies,
        launch=_parse_launch(payload.get("launch")),
        endpoint=_parse_endpoint(payload.get("endpoint")),
        model_routing=_parse_model_routing(payload.get("model_routing")),
        smoke_probe=_parse_smoke_probe(payload.get("smoke_probe")),
    )
