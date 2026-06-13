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
class AgentRuntimeConfig:
    """Parsed agent runtime-config."""

    id: str
    roots: list[ConfigRoot]
    display_name: str | None = None
    schema_version: int = 1
    exclude: list[str] = field(default_factory=list)
    instruction_files: list[str] = field(default_factory=list)
    external_dependencies: list[ConfigRoot] = field(default_factory=list)


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
    )
