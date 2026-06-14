# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Local agent snapshot helpers for sandboxed endpoint eval setup.

Snapshots copy discovered and user-approved local roots into a disposable directory. The copy
step is intentionally generic: discovered home/config/workspace roots are copied by
default, callers can add extra roots with ``--include-root``, and advanced callers
can still provide explicit ``source:dest`` mappings.
"""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
import json
from pathlib import Path
import re
import shutil
from typing import Any, Iterable

from assert_ai.local_agent_config import AgentRuntimeConfig


SECRET_DATA_SUFFIXES = {"", ".env", ".json", ".pem", ".key", ".p12", ".pfx", ".txt", ".yaml", ".yml"}


@dataclass(frozen=True)
class LocalAgentSnapshotResult:
    manifest_path: Path
    snapshot_root: Path
    manifest: dict[str, Any]


def _path_value(path: Path | None, *, redact_paths: bool) -> str | None:
    if path is None:
        return None
    return "[LOCAL_PATH]" if redact_paths else str(path)


def _safe_relative_dest(dest: str) -> Path:
    dest_path = Path(dest)
    if not dest or dest_path.is_absolute() or ".." in dest_path.parts:
        raise ValueError("copy-root destination must be relative and stay inside the snapshot")
    return dest_path


def _parse_copy_root_spec(spec: str) -> tuple[Path, Path]:
    if ":" not in spec:
        raise ValueError("copy-root must be SOURCE:DEST")
    source_raw, dest_raw = spec.rsplit(":", 1)
    if not source_raw or not dest_raw:
        raise ValueError("copy-root must be SOURCE:DEST")
    source = Path(source_raw).expanduser().resolve()
    if not source.exists():
        raise ValueError(f"copy-root source does not exist: {source_raw}")
    dest = _safe_relative_dest(dest_raw)
    return source, dest


def _safe_default_dest(path: Path) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", path.name).strip(".-") or "included-root"
    return _safe_relative_dest(safe)


def _parse_include_root_spec(spec: str) -> tuple[Path, Path]:
    source = Path(spec).expanduser().resolve()
    if not source.exists():
        raise ValueError(f"include-root source does not exist: {spec}")
    return source, _safe_default_dest(source)


def _is_redacted_path(value: Any) -> bool:
    return value in {None, "", "[LOCAL_PATH]", "[PATH_ENTRY]"}


def _agent_path(agent: dict[str, Any], section: str, field: str) -> Path | None:
    value = (agent.get(section) or {}).get(field)
    if _is_redacted_path(value):
        return None
    return Path(str(value)).expanduser().resolve()


def _resolve_source_selector(agent: dict[str, Any], selector: str) -> Path | None:
    """Resolve a dotted ``section.field`` selector (e.g. ``workspace.path``)."""

    section, _, field = selector.partition(".")
    if not section or not field:
        return None
    return _agent_path(agent, section, field)


def _declared_snapshot_default_roots(agent: dict[str, Any]) -> list[tuple[Path, Path]]:
    """Return default roots from an agent's declared ``snapshot_defaults``.

    Each entry declares a ``source`` selector (``section.field``) plus a stable
    relative ``dest``. When the source path is redacted, an optional
    ``home_fallback`` resolves the root against the current home. The snapshot
    code stays generic: runtimes declare their own required layout instead of
    being special-cased here.
    """

    declared = agent.get("snapshot_defaults")
    if not isinstance(declared, list):
        return []
    home = Path.home()
    roots: list[tuple[Path, Path]] = []
    for entry in declared:
        if not isinstance(entry, dict):
            continue
        selector = str(entry.get("source") or "")
        dest_raw = str(entry.get("dest") or "")
        if not dest_raw:
            continue
        source = _resolve_source_selector(agent, selector) if selector else None
        if source is None:
            fallback = entry.get("home_fallback")
            if isinstance(fallback, str) and fallback:
                source = (home / fallback).expanduser().resolve()
        if source is None or not source.exists():
            continue
        roots.append((source, _safe_relative_dest(dest_raw)))
    return roots


def _safe_hidden_dest(path: Path) -> Path:
    name = path.name
    if name.startswith(".") and name not in {".", ".."}:
        return _safe_relative_dest(name)
    return _safe_default_dest(path)


def _known_config_root(agent_id: str) -> Path | None:
    home = Path.home()
    return {
        "hermes": home / ".hermes",
        "claude-code": home / ".claude",
        "codex": home / ".codex",
        "opencode": home / ".config" / "opencode",
        "gemini": home / ".gemini",
    }.get(agent_id)


def _default_discovered_roots(agent: dict[str, Any]) -> list[tuple[Path, Path]]:
    """Return generic default roots from shallow discovery evidence."""

    roots: list[tuple[Path, Path]] = []
    seen: set[Path] = set()
    agent_id = str(agent.get("id") or "")
    for section in ("home", "config", "workspace"):
        section_payload = agent.get(section)
        if not isinstance(section_payload, dict):
            continue
        source = _agent_path(agent, section, "path")
        exists = bool(section_payload.get("exists", True))
        if source is None and section == "config" and exists:
            source = _known_config_root(agent_id)
        if source is None or not exists or not source.exists() or source in seen:
            continue
        roots.append((source, _safe_hidden_dest(source)))
        seen.add(source)
    return roots


def _matches_secret_pattern(rel: str, name: str, patterns: Iterable[str] = ()) -> bool:
    lower_rel = rel.lower()
    lower_name = name.lower()
    suffix = Path(lower_name).suffix
    if lower_name == ".env" or lower_name.endswith(".env") or "/.env" in lower_rel:
        return True
    if ("credential" in lower_name or "token" in lower_name) and suffix in SECRET_DATA_SUFFIXES:
        return True
    if "secret" in lower_name and suffix in SECRET_DATA_SUFFIXES:
        return True
    for pattern in patterns:
        lower_pattern = pattern.lower()
        if fnmatch(lower_rel, lower_pattern) or fnmatch(lower_name, lower_pattern):
            return True
    return False


def _copy_root(*, source: Path, dest: Path, snapshot_root: Path, exclude_patterns: Iterable[str]) -> tuple[int, int, list[dict[str, Any]]]:
    destination_root = snapshot_root / dest
    files_copied = 0
    bytes_copied = 0
    excluded: list[dict[str, Any]] = []

    def copy_file(src: Path, rel: Path) -> None:
        nonlocal files_copied, bytes_copied
        rel_posix = rel.as_posix()
        dest_posix = (dest / rel).as_posix()
        if _matches_secret_pattern(rel_posix, src.name, exclude_patterns):
            excluded.append({"source": rel_posix, "dest": dest_posix, "reason": "secret_or_credential_like_path"})
            return
        if src.is_symlink():
            # Directory symlinks are not dereferenced: following them risks
            # cycles and silently pulling in huge external trees.
            if src.is_dir():
                excluded.append({"source": rel_posix, "dest": dest_posix, "reason": "directory_symlink_not_copied"})
                return
            # File symlinks ARE dereferenced. A runtime's interpreter is often a
            # symlink (e.g. a venv's bin/python -> an externally managed cpython),
            # so the clone needs the real target content, not a dangling link.
            if not src.exists():
                excluded.append({"source": rel_posix, "dest": dest_posix, "reason": "broken_symlink"})
                return
        target = destination_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        # follow_symlinks=True (default) copies the real target content.
        shutil.copy2(src, target, follow_symlinks=True)
        files_copied += 1
        try:
            bytes_copied += target.stat().st_size
        except OSError:
            pass

    if source.is_file():
        copy_file(source, Path(source.name))
        return files_copied, bytes_copied, excluded

    for path in sorted(source.rglob("*")):
        if path.is_dir() and not path.is_symlink():
            continue
        try:
            rel = path.relative_to(source)
        except ValueError:
            continue
        copy_file(path, rel)

    return files_copied, bytes_copied, excluded


def _load_discovery(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read discovery manifest: {path}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("agents"), list):
        raise ValueError("discovery manifest must contain an agents list")
    return payload


def _select_agent(discovery: dict[str, Any], target: str) -> dict[str, Any]:
    for agent in discovery.get("agents", []):
        if isinstance(agent, dict) and agent.get("id") == target:
            return agent
    raise ValueError(f"target not found in discovery manifest: {target}")


def create_local_agent_snapshot(
    *,
    discovery_path: str | Path,
    target: str,
    copy_root_specs: Iterable[str],
    output_dir: str | Path,
    include_root_specs: Iterable[str] = (),
    redact_paths: bool = True,
) -> LocalAgentSnapshotResult:
    """Create a copied snapshot for a discovered local agent.

    ``copy_root_specs`` are advanced explicit ``SOURCE:DEST`` entries.
    Runtime-specific defaults may contribute required roots for supported
    targets, and ``include_root_specs`` lets users add extra context roots
    without deciding the snapshot destination.
    """

    discovery_file = Path(discovery_path).expanduser().resolve()
    output = Path(output_dir).expanduser().resolve()
    discovery = _load_discovery(discovery_file)
    agent = _select_agent(discovery, target)

    copy_roots: list[tuple[Path, Path]] = []
    copy_root_values = tuple(copy_root_specs)
    include_root_values = tuple(include_root_specs)
    used_default_runtime_roots = False
    # Prefer the runtime's own declared snapshot layout (e.g. OpenClaw's
    # workspace + runtime-package roots). This keeps the snapshot path generic:
    # runtimes declare their required roots in discovery, instead of being
    # special-cased here by target id. Fall back to generic discovered
    # home/config/workspace roots when nothing is declared.
    default_roots = _declared_snapshot_default_roots(agent) or _default_discovered_roots(agent)
    if default_roots:
        copy_roots.extend(default_roots)
        used_default_runtime_roots = True
    copy_roots.extend(_parse_copy_root_spec(spec) for spec in copy_root_values)
    copy_roots.extend(_parse_include_root_spec(spec) for spec in include_root_values)
    if not copy_roots:
        raise ValueError("at least one default root, --include-root, or --copy-root is required")

    snapshot_root = output / "snapshot"
    if snapshot_root.exists():
        shutil.rmtree(snapshot_root)
    snapshot_root.mkdir(parents=True, exist_ok=True)

    copied_roots: list[dict[str, Any]] = []
    excluded_files: list[dict[str, Any]] = []
    for source, dest in copy_roots:
        files_copied, bytes_copied, excluded = _copy_root(
            source=source,
            dest=dest,
            snapshot_root=snapshot_root,
            exclude_patterns=(),
        )
        copied_roots.append(
            {
                "source": _path_value(source, redact_paths=redact_paths),
                "dest": dest.as_posix(),
                "files_copied": files_copied,
                "bytes_copied": bytes_copied,
            }
        )
        excluded_files.extend(excluded)

    manifest = {
        "schema_version": 1,
        "target": target,
        "agent": {
            "id": agent.get("id"),
            "display_name": agent.get("display_name"),
            "kind": agent.get("kind"),
            "status": agent.get("status"),
        },
        "discovery_manifest": _path_value(discovery_file, redact_paths=redact_paths),
        "snapshot_root": "snapshot",
        "copied_roots": copied_roots,
        "excluded_files": sorted(excluded_files, key=lambda item: item["dest"]),
        "safety": {
            "default_runtime_roots": used_default_runtime_roots,
            "explicit_include_roots": bool(include_root_values),
            "advanced_copy_roots_used": bool(copy_root_values),
            "secrets_excluded_by_path": True,
            "file_symlinks_dereferenced": True,
        },
    }

    manifest_path = output / "snapshot_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return LocalAgentSnapshotResult(manifest_path=manifest_path, snapshot_root=snapshot_root, manifest=manifest)


# ---- agent-config-driven snapshots ------------------------------------------
# The config path is the agent-config pivot: an agent declares what to copy in a
# runtime-config (real-machine paths), and ASSERT translates those into a
# sandbox snapshot. Reuses the same copy machinery; the difference is the source
# of roots/excludes and the built-in secret floor applied to every copy.

# Built-in exclude floor: secrets + pure churn only. Always applied on top of
# config-declared excludes so a config cannot accidentally opt into copying
# credentials. Decision (2026-06-13): we copy dependencies BLINDLY -- node_modules,
# venv, and other runtime dependency dirs are exactly what a runtime needs to boot,
# so the floor must NOT strip them. Junk reduction happens at the source (the agent's
# self-report prompt), never by guessing here. The floor only drops things that can
# never be a runtime dependency: secrets and pure churn (sessions/logs/.git).
BUILTIN_EXCLUDE_FLOOR: tuple[str, ...] = (
    "*.env",
    ".env",
    "auth.json",
    "auth.json.bak*",
    "*.pem",
    "*.key",
    "sessions/**",
    "logs/**",
    "**/.git/**",
)


def _normalize_excludes_for_root(patterns: Iterable[str], *, source: Path) -> list[str]:
    """Translate absolute-path excludes that fall under ``source`` into root-relative globs.

    Agents emit REAL (absolute) paths in their exclude lists (decision 2026-06-13),
    but ``_copy_root`` matches against paths relative to the root source. An absolute
    pattern like ``/home/me/.openclaw/workspace/notes.md`` would never fire. Here we
    rewrite it to ``workspace/notes.md`` so it matches. Patterns that are already
    relative globs, or absolute paths outside this root, pass through unchanged (the
    latter simply won't match anything in this root, which is correct).
    """

    try:
        resolved_source = source.expanduser().resolve()
    except OSError:
        resolved_source = source
    normalized: list[str] = []
    for pattern in patterns:
        pattern_path = Path(pattern).expanduser()
        if pattern_path.is_absolute():
            try:
                rel = pattern_path.resolve().relative_to(resolved_source)
            except (ValueError, OSError):
                normalized.append(pattern)
                continue
            normalized.append(rel.as_posix())
        else:
            normalized.append(pattern)
    return normalized


def create_snapshot_from_config(
    *,
    config: AgentRuntimeConfig,
    output_dir: str | Path,
    redact_paths: bool = True,
) -> LocalAgentSnapshotResult:
    """Create a snapshot from an agent runtime-config.

    Roots come from ``config.roots`` (real-machine ``source`` paths). Each root's
    ``dest`` is used verbatim, or derived from the source dir name when omitted.
    Excludes are the built-in secret floor + global ``config.exclude`` +
    per-root excludes. ``instruction_files`` are recorded (still source-relative)
    for spec build to translate after staging.
    """

    if not config.roots:
        raise ValueError("agent config must declare at least one root")

    output = Path(output_dir).expanduser().resolve()
    snapshot_root = output / "snapshot"
    if snapshot_root.exists():
        shutil.rmtree(snapshot_root)
    snapshot_root.mkdir(parents=True, exist_ok=True)

    copied_roots: list[dict[str, Any]] = []
    excluded_files: list[dict[str, Any]] = []
    # External dependencies are copy roots too (decision 2026-06-13): the agent needs
    # them to run, so they must land in the snapshot, not be silently ignored.
    all_roots = [*config.roots, *config.external_dependencies]
    for root in all_roots:
        source = root.source.expanduser().resolve()
        if not source.exists():
            if root.required:
                raise ValueError(f"required root source does not exist: {root.source}")
            continue
        dest = _safe_relative_dest(root.dest) if root.dest else _safe_hidden_dest(source)
        # Absolute-path excludes (which agents naturally emit) are normalized to
        # root-relative globs so they actually fire against this root's copy walk.
        raw_patterns = (*config.exclude, *root.exclude)
        normalized = _normalize_excludes_for_root(raw_patterns, source=source)
        exclude_patterns = (*BUILTIN_EXCLUDE_FLOOR, *normalized)
        files_copied, bytes_copied, excluded = _copy_root(
            source=source,
            dest=dest,
            snapshot_root=snapshot_root,
            exclude_patterns=exclude_patterns,
        )
        copied_roots.append(
            {
                "source": _path_value(source, redact_paths=redact_paths),
                "dest": dest.as_posix(),
                "files_copied": files_copied,
                "bytes_copied": bytes_copied,
                "kind": root.kind,
            }
        )
        excluded_files.extend(excluded)

    manifest = {
        "schema_version": 1,
        "source": "agent_config",
        "target": config.id,
        "agent": {
            "id": config.id,
            "display_name": config.display_name,
        },
        "snapshot_root": "snapshot",
        "copied_roots": copied_roots,
        "instruction_files": list(config.instruction_files),
        "excluded_files": sorted(excluded_files, key=lambda item: item["dest"]),
        "safety": {
            "builtin_secret_floor": True,
            "config_excludes": list(config.exclude),
            "secrets_excluded_by_path": True,
            "file_symlinks_dereferenced": True,
        },
    }

    manifest_path = output / "snapshot_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return LocalAgentSnapshotResult(manifest_path=manifest_path, snapshot_root=snapshot_root, manifest=manifest)
