# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Local agent snapshot helpers for sandboxed endpoint eval setup.

Snapshots copy user-approved local roots into a disposable directory. The copy
step is intentionally generic: callers provide explicit ``source:dest`` roots;
this module preserves directory structure under the snapshot, skips obvious
secret/credential-looking files, and writes a reviewable manifest.
"""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
import json
from pathlib import Path
import shutil
from typing import Any, Iterable

from assert_ai.local_agents import DEFAULT_EXCLUDE_PATTERNS


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


def _matches_secret_pattern(rel: str, name: str, patterns: Iterable[str]) -> bool:
    lower_rel = rel.lower()
    lower_name = name.lower()
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
            excluded.append({"source": rel_posix, "dest": dest_posix, "reason": "symlink_not_copied"})
            return
        target = destination_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
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
    redact_paths: bool = True,
) -> LocalAgentSnapshotResult:
    """Create a copied snapshot for a discovered local agent.

    ``copy_root_specs`` are explicit ``SOURCE:DEST`` entries. Discovery hints are
    not treated as authoritative; the caller decides which roots matter.
    """

    discovery_file = Path(discovery_path).expanduser().resolve()
    output = Path(output_dir).expanduser().resolve()
    copy_roots = [_parse_copy_root_spec(spec) for spec in copy_root_specs]
    if not copy_roots:
        raise ValueError("at least one --copy-root is required")

    discovery = _load_discovery(discovery_file)
    agent = _select_agent(discovery, target)

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
            exclude_patterns=DEFAULT_EXCLUDE_PATTERNS,
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
            "explicit_copy_roots_only": True,
            "secrets_excluded_by_path": True,
            "symlinks_not_copied": True,
        },
    }

    manifest_path = output / "snapshot_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return LocalAgentSnapshotResult(manifest_path=manifest_path, snapshot_root=snapshot_root, manifest=manifest)
