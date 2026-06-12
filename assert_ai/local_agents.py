# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Local agent discovery helpers for sandboxed endpoint eval setup.

This module intentionally performs shallow, allowlisted discovery. It does not
crawl home directories or read credential files. The output is meant to be a
reviewable first step before creating a copied sandbox snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
import json
from pathlib import Path
import shutil
from typing import Any, Iterable

DEFAULT_EXCLUDE_PATTERNS = (
    ".env",
    "*.env",
    "**/*.env",
    "*token*",
    "*credential*",
    "*secret*",
)

DEFAULT_OPENCLAW_INCLUDE = (
    "AGENTS.md",
    "SOUL.md",
    "USER.md",
    "TOOLS.md",
    "MEMORY.md",
)

_COMMON_AGENT_IDS = {"openclaw", "hermes", "claude-code", "codex", "opencode", "gemini"}


@dataclass(frozen=True)
class LocalAgentDiscoveryResult:
    agents: tuple[dict[str, Any], ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "agents": list(self.agents),
        }


def _path_value(path: Path | None, *, redact_paths: bool) -> str | None:
    if path is None:
        return None
    return "[LOCAL_PATH]" if redact_paths else str(path)


def _safe_relative(pattern: str) -> bool:
    path = Path(pattern)
    return not path.is_absolute() and ".." not in path.parts


def _load_source_manifest(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _matches_any(path: Path, workspace: Path, patterns: Iterable[str]) -> bool:
    rel = path.relative_to(workspace).as_posix()
    return any(fnmatch(rel, pattern) or fnmatch(path.name, pattern) for pattern in patterns)


def _expand_patterns(workspace: Path, patterns: Iterable[str]) -> list[Path]:
    paths: dict[str, Path] = {}
    for pattern in patterns:
        if not _safe_relative(pattern):
            continue
        matches = sorted(workspace.glob(pattern)) if any(ch in pattern for ch in "*?[") else [workspace / pattern]
        for match in matches:
            if match.exists() and match.is_file():
                paths[match.relative_to(workspace).as_posix()] = match
    return [paths[key] for key in sorted(paths)]


def _candidate_and_excluded_files(
    *,
    workspace: Path,
    source_bundle_path: Path | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], tuple[str, ...]]:
    manifest = _load_source_manifest(source_bundle_path)
    include = tuple(str(item) for item in (manifest.get("include") or DEFAULT_OPENCLAW_INCLUDE))
    exclude_patterns = tuple(str(item) for item in (manifest.get("exclude_patterns") or DEFAULT_EXCLUDE_PATTERNS))

    candidate_files: list[dict[str, Any]] = []
    excluded_files: list[dict[str, Any]] = []
    if not workspace.exists():
        return candidate_files, excluded_files, exclude_patterns

    candidate_source = "source_bundle" if source_bundle_path is not None and source_bundle_path.exists() else "default_include"
    for path in _expand_patterns(workspace, include):
        rel = path.relative_to(workspace).as_posix()
        if _matches_any(path, workspace, exclude_patterns):
            excluded_files.append({"path": rel, "reason": "excluded_by_source_bundle"})
            continue
        try:
            size_bytes = path.stat().st_size
        except OSError:
            size_bytes = 0
        candidate_files.append({"path": rel, "size_bytes": size_bytes, "source": candidate_source})

    # Also surface obvious secret-looking files even if they were not included,
    # without reading their contents.
    for pattern in exclude_patterns:
        if not _safe_relative(pattern):
            continue
        for path in _expand_patterns(workspace, (pattern,)):
            rel = path.relative_to(workspace).as_posix()
            if rel not in {item["path"] for item in excluded_files}:
                excluded_files.append({"path": rel, "reason": "secret_or_credential_like_path"})

    excluded_files.sort(key=lambda item: item["path"])
    return candidate_files, excluded_files, exclude_patterns


def _read_package_version(runtime_path: Path) -> str | None:
    package_json = runtime_path / "package.json"
    if not package_json.exists():
        return None
    try:
        data = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    version = data.get("version")
    return str(version) if version else None


def _openclaw_agent(
    *,
    home: Path,
    runtime_path: Path | None,
    workspace_path: Path | None,
    source_bundle_path: Path | None,
    redact_paths: bool,
) -> dict[str, Any] | None:
    runtime = (runtime_path or home / ".npm-global" / "lib" / "node_modules" / "openclaw").expanduser().resolve()
    workspace = (workspace_path or home / ".openclaw" / "workspace").expanduser().resolve()
    source_bundle = (source_bundle_path or workspace / "source-bundle.json").expanduser().resolve()
    runtime_valid = (runtime / "package.json").exists() and (runtime / "openclaw.mjs").exists()
    workspace_exists = workspace.exists()
    source_bundle_exists = source_bundle.exists()
    if not (runtime_valid or workspace_exists or runtime_path or workspace_path):
        return None

    candidate_files, excluded_files, exclude_patterns = _candidate_and_excluded_files(
        workspace=workspace,
        source_bundle_path=source_bundle if source_bundle_exists else None,
    )
    status = "ready" if runtime_valid and workspace_exists else "found"
    return {
        "id": "openclaw",
        "display_name": "OpenClaw",
        "kind": "openclaw",
        "status": status,
        "summary": "ready for snapshot" if status == "ready" else "found but missing runtime or workspace pieces",
        "runtime": {
            "name": "openclaw",
            "version": _read_package_version(runtime),
            "path": _path_value(runtime, redact_paths=redact_paths),
            "valid": runtime_valid,
            "copy_strategy": "local_package_snapshot",
        },
        "workspace": {
            "path": _path_value(workspace, redact_paths=redact_paths),
            "exists": workspace_exists,
        },
        "source_bundle": {
            "path": _path_value(source_bundle if source_bundle_exists else None, redact_paths=redact_paths),
            "exists": source_bundle_exists,
        },
        "candidate_files": candidate_files,
        "excluded_files": excluded_files,
        "excluded_patterns": list(exclude_patterns),
    }


def _config_agent(
    *,
    agent_id: str,
    display_name: str,
    config_path: Path,
    binary_name: str | None,
    redact_paths: bool,
    notes: list[str] | None = None,
) -> dict[str, Any] | None:
    binary = shutil.which(binary_name) if binary_name else None
    config_exists = config_path.exists()
    if not (config_exists or binary):
        return None
    return {
        "id": agent_id,
        "display_name": display_name,
        "kind": agent_id,
        "status": "found",
        "summary": "found local config or executable; snapshot support is not implemented yet",
        "config": {
            "path": _path_value(config_path, redact_paths=redact_paths),
            "exists": config_exists,
        },
        "runtime": {
            "binary": binary_name,
            "binary_path": "[PATH_ENTRY]" if redact_paths and binary else binary,
            "valid": bool(binary),
        },
        "notes": notes or [],
    }


def discover_local_agents(
    *,
    target: str | None = None,
    home: str | Path | None = None,
    runtime_path: str | Path | None = None,
    workspace_path: str | Path | None = None,
    source_bundle_path: str | Path | None = None,
    redact_paths: bool = True,
) -> LocalAgentDiscoveryResult:
    """Discover local agent runtimes/configs without crawling the machine."""

    selected = (target or "all").lower()
    if selected != "all" and selected not in _COMMON_AGENT_IDS:
        raise ValueError(f"unknown local agent target: {target}")

    home_path = Path(home).expanduser().resolve() if home is not None else Path.home()
    runtime = Path(runtime_path).expanduser().resolve() if runtime_path is not None else None
    workspace = Path(workspace_path).expanduser().resolve() if workspace_path is not None else None
    source_bundle = Path(source_bundle_path).expanduser().resolve() if source_bundle_path is not None else None

    candidates: list[dict[str, Any] | None] = []
    if selected in {"all", "openclaw"}:
        candidates.append(
            _openclaw_agent(
                home=home_path,
                runtime_path=runtime,
                workspace_path=workspace,
                source_bundle_path=source_bundle,
                redact_paths=redact_paths,
            )
        )
    if selected in {"all", "hermes"}:
        candidates.append(
            _config_agent(
                agent_id="hermes",
                display_name="Hermes",
                config_path=home_path / ".hermes",
                binary_name="hermes",
                redact_paths=redact_paths,
                notes=["Hermes local API endpoints commonly run on localhost when the gateway/api server is enabled."],
            )
        )
    if selected in {"all", "claude-code"}:
        candidates.append(
            _config_agent(
                agent_id="claude-code",
                display_name="Claude Code",
                config_path=home_path / ".claude",
                binary_name="claude",
                redact_paths=redact_paths,
                notes=["Claude Code uses ~/.claude for user configuration on common installs."],
            )
        )
    if selected in {"all", "codex"}:
        candidates.append(
            _config_agent(
                agent_id="codex",
                display_name="Codex CLI",
                config_path=home_path / ".codex",
                binary_name="codex",
                redact_paths=redact_paths,
                notes=["Codex CLI commonly stores local config/session state under ~/.codex."],
            )
        )
    if selected in {"all", "opencode"}:
        candidates.append(
            _config_agent(
                agent_id="opencode",
                display_name="OpenCode",
                config_path=home_path / ".config" / "opencode",
                binary_name="opencode",
                redact_paths=redact_paths,
                notes=["OpenCode global config commonly lives under ~/.config/opencode; projects may also have .opencode."],
            )
        )
    if selected in {"all", "gemini"}:
        candidates.append(
            _config_agent(
                agent_id="gemini",
                display_name="Gemini CLI",
                config_path=home_path / ".gemini",
                binary_name="gemini",
                redact_paths=redact_paths,
                notes=["Gemini CLI commonly uses ~/.gemini/settings.json for settings and MCP server configuration."],
            )
        )

    return LocalAgentDiscoveryResult(agents=tuple(agent for agent in candidates if agent is not None))
