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
import re
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

REFERENCE_FILE_NAMES = set(DEFAULT_OPENCLAW_INCLUDE) | {
    "config.json",
    "config.toml",
    "config.yaml",
    "config.yml",
    "settings.json",
    "source-bundle.json",
}
REFERENCE_FILE_SUFFIXES = {".json", ".jsonc", ".md", ".toml", ".yaml", ".yml"}
MAX_REFERENCE_FILE_BYTES = 64 * 1024
ABSOLUTE_PATH_RE = re.compile(r"(?:[A-Za-z]:\\[^\s'\"<>]+|/[A-Za-z0-9._~+\-/]+)")
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


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _safe_copy_root_dest(path: Path) -> str:
    name = path.name or "external-root"
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip(".-") or "external-root"
    return f"external/{safe}"


def _path_parts(path: Path) -> tuple[str, ...]:
    return tuple(part.lower() for part in path.parts if part not in {path.anchor, "/"})


def _is_low_value_copy_root(path: Path) -> bool:
    parts = _path_parts(path)
    if not parts:
        return True
    if str(path) == "/":
        return True
    system_prefixes = (
        ("bin",),
        ("boot",),
        ("dev",),
        ("etc",),
        ("lib",),
        ("lib64",),
        ("proc",),
        ("run",),
        ("sbin",),
        ("sys",),
        ("usr",),
        ("var",),
        ("mnt", "c", "users"),
    )
    if any(parts[: len(prefix)] == prefix for prefix in system_prefixes):
        return True
    if len(parts) >= 2 and parts[-2] in {"home", "users"}:
        return True
    return False


def _is_high_confidence_copy_root(*, kind: str, target: Path) -> bool:
    if kind == "symlink" and not _is_low_value_copy_root(target):
        return True
    if (target / ".git").exists():
        return True
    if _is_low_value_copy_root(target):
        return False
    name = target.name.lower()
    parts = set(_path_parts(target))
    signal_words = {
        "context",
        "contexts",
        "knowledge",
        "memory",
        "memories",
        "project",
        "projects",
        "repo",
        "repos",
        "workspace",
        "workspaces",
        "work",
    }
    if any(word in parts for word in signal_words):
        return True
    return any(word in name for word in signal_words)


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


def _external_references(
    *,
    workspace: Path,
    exclude_patterns: Iterable[str],
    redact_paths: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Find shallow out-of-root references without following or copying them.

    This is intentionally generic: symlinks that leave the workspace and obvious
    absolute path values in small root-level config/instruction files are surfaced
    as hints for later ``--copy-root`` selection. Discovery does not crawl the
    referenced roots and does not decide that they must be copied.
    """

    if not workspace.exists() or not workspace.is_dir():
        return [], []

    workspace_root = workspace.resolve()
    references: list[dict[str, Any]] = []
    seen_references: set[tuple[str, str, str]] = set()
    suggested_roots: dict[str, dict[str, Any]] = {}

    def add_reference(*, source: str, kind: str, target: Path) -> None:
        try:
            resolved = target.expanduser().resolve(strict=False)
        except OSError:
            resolved = target.expanduser().absolute()
        if not resolved.is_absolute():
            return
        if resolved == workspace_root or _is_relative_to(resolved, workspace_root):
            return
        key = (source, kind, str(resolved))
        if key in seen_references:
            return
        seen_references.add(key)
        references.append(
            {
                "source": source,
                "kind": kind,
                "path": _path_value(resolved, redact_paths=redact_paths),
                "exists": resolved.exists(),
            }
        )
        if _is_high_confidence_copy_root(kind=kind, target=resolved):
            suggested_roots.setdefault(
                str(resolved),
                {
                    "source": _path_value(resolved, redact_paths=redact_paths),
                    "dest": _safe_copy_root_dest(resolved),
                    "reason": "external_reference",
                },
            )

    # Symlink scan stays inside the selected workspace tree. ``rglob`` lists the
    # symlink itself but does not require reading or copying the target root.
    try:
        workspace_paths = list(workspace.rglob("*"))
    except OSError:
        workspace_paths = []
    for path in sorted(workspace_paths):
        if not path.is_symlink():
            continue
        try:
            source = path.relative_to(workspace).as_posix()
        except ValueError:
            source = path.name
        add_reference(source=source, kind="symlink", target=path.resolve(strict=False))

    # Path-value detection is deliberately shallow: only small root-level files
    # that look like instructions or config are read, and secret-looking files are
    # skipped by name.
    try:
        root_files = sorted(workspace.iterdir())
    except OSError:
        root_files = []
    for path in root_files:
        if not path.is_file() or path.is_symlink():
            continue
        if _matches_any(path, workspace, exclude_patterns):
            continue
        if path.name not in REFERENCE_FILE_NAMES and path.suffix.lower() not in REFERENCE_FILE_SUFFIXES:
            continue
        try:
            if path.stat().st_size > MAX_REFERENCE_FILE_BYTES:
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        source = path.relative_to(workspace).as_posix()
        for match in ABSOLUTE_PATH_RE.findall(text):
            candidate = Path(match.rstrip(".,;:)]}"))
            if candidate.exists():
                add_reference(source=source, kind="path_value", target=candidate)

    references.sort(key=lambda item: (item["source"], item["kind"], item["path"] or ""))
    copy_roots = sorted(suggested_roots.values(), key=lambda item: (item["dest"], item["source"] or ""))
    return references, copy_roots


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
    external_references, suggested_copy_roots = _external_references(
        workspace=workspace,
        exclude_patterns=exclude_patterns,
        redact_paths=redact_paths,
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
        "external_references": external_references,
        "suggested_copy_roots": suggested_copy_roots,
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
