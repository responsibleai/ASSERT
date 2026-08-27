# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Persist lightweight suite/run metadata and derived JSONL indexes."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from assert_ai.core.io import load_json, load_jsonl, write_json
from assert_ai.core.jsonl_index import (
    JsonlIndexError,
    JsonlIndexErrorCode,
    build_jsonl_index,
    jsonl_index_path,
    load_jsonl_index,
    scan_jsonl,
)
from assert_ai.results import (
    compute_prompt_metrics,
    compute_scenario_metrics,
)

SUITE_SUMMARY_SCHEMA_VERSION = 1
RUN_SUMMARY_SCHEMA_VERSION = 1
RUN_CATALOG_SCHEMA_VERSION = 1
RUN_CATALOG_FILENAME = "run_catalog.json"


def refresh_stage_indexes(
    ctx: dict[str, Any],
    stage_name: str,
    stage_result: dict[str, Any] | None = None,
) -> None:
    """Create or refresh the canonical JSONL index produced by one stage."""
    stage_result = stage_result or {}
    if stage_name == "test_set":
        source = _optional_path(
            stage_result.get("test_set_path") or ctx.get("test_set_path")
        )
    elif stage_name == "inference":
        source = _optional_path(
            stage_result.get("inference_set_path")
            or ctx.get("inference_set_path")
            or _under_run_root(ctx, "inference_set.jsonl")
        )
        if source is not None:
            ctx["inference_set_path"] = str(source)
    elif stage_name == "judge":
        source = _optional_path(
            stage_result.get("scores_path")
            or ctx.get("scores_path")
            or _under_run_root(ctx, "scores.jsonl")
        )
        if source is not None:
            ctx["scores_path"] = str(source)
    else:
        return
    if source is not None and source.is_file():
        _ensure_jsonl_index(source)


def write_run_summary(
    ctx: dict[str, Any],
    manifest: Any,
    *,
    stage_summaries: dict[str, dict[str, Any]] | None = None,
    metrics: dict[str, Any] | None = None,
    rebuild_indexes: bool = True,
) -> dict[str, Any] | None:
    """Write one metadata-only run summary without embedding source rows."""
    run_root = _optional_path(ctx.get("run_root"))
    suite_root = _optional_path(ctx.get("suite_root"))
    if run_root is None or suite_root is None:
        return None
    run_root.mkdir(parents=True, exist_ok=True)

    summary_path = run_root / "run_summary.json"
    previous = _load_optional_json(summary_path) or {}
    manifest_payload = (
        manifest.to_dict()
        if hasattr(manifest, "to_dict")
        else dict(manifest or {})
    )
    status = str(manifest_payload.get("status") or "unknown")
    current_stage = _current_or_terminal_stage(manifest_payload)

    sources: dict[str, Any] = {}
    indexes: dict[str, dict[str, Any]] = {}
    counts: dict[str, Any] = {}

    source_paths = {
        "taxonomy": _optional_path(
            ctx.get("taxonomy_path") or suite_root / "taxonomy.json"
        ),
        "test_set": _optional_path(
            ctx.get("test_set_path") or suite_root / "test_set.jsonl"
        ),
        "inference_set": _optional_path(
            ctx.get("inference_set_path") or run_root / "inference_set.jsonl"
        ),
        "scores": _optional_path(
            ctx.get("scores_path") or run_root / "scores.jsonl"
        ),
    }

    jsonl_payloads: dict[str, dict[str, Any]] = {}
    for name, path in source_paths.items():
        if path is None or not path.is_file():
            continue
        if path.suffix == ".jsonl":
            index = _load_or_build_jsonl_index(path, rebuild=rebuild_indexes)
            if index is not None:
                jsonl_payloads[name] = index
                indexes[name] = {
                    "schema_version": index["schema_version"],
                    **_path_reference(
                        jsonl_index_path(path),
                        suite_root=suite_root,
                        run_root=run_root,
                        ctx=ctx,
                    ),
                }
                sources[name] = {
                    **_path_reference(
                        path,
                        suite_root=suite_root,
                        run_root=run_root,
                        ctx=ctx,
                    ),
                    **index["source"],
                    "index_schema_version": index["schema_version"],
                }
                continue
        sources[name] = _file_identity(
            path,
            suite_root=suite_root,
            run_root=run_root,
            ctx=ctx,
        )

    for name in ("test_set", "inference_set", "scores"):
        index = jsonl_payloads.get(name)
        if index is not None:
            counts[name] = _index_counts(index)

    taxonomy = (
        _load_optional_json(source_paths["taxonomy"])
        if source_paths["taxonomy"] is not None
        else None
    )
    behavior_categories = (
        taxonomy.get("behavior_categories")
        if isinstance(taxonomy, dict)
        else None
    )
    if not isinstance(behavior_categories, list):
        behavior_categories = []

    quality = previous.get("quality")
    if rebuild_indexes and source_paths["scores"] is not None:
        score_rows = load_jsonl(source_paths["scores"])
        prompt_rows = [row for row in score_rows if not row.get("tester_model")]
        scenario_rows = [row for row in score_rows if row.get("tester_model")]
        quality = {
            "prompt": compute_prompt_metrics(prompt_rows, behavior_categories),
            "scenario": compute_scenario_metrics(
                scenario_rows,
                behavior_categories,
            ),
        }

    payload = {
        "schema_version": RUN_SUMMARY_SCHEMA_VERSION,
        "suite_id": str(ctx.get("suite_id") or suite_root.name),
        "run_id": str(ctx.get("run_id") or run_root.name),
        "state": status,
        "current_stage": current_stage,
        "started_at": manifest_payload.get("started_at"),
        "ended_at": manifest_payload.get("ended_at"),
        "updated_at": _utc_now(),
        "stages": manifest_payload.get("stages") or {},
        "stage_timings": manifest_payload.get("stage_timings") or {},
        "stage_summaries": _sanitize_managed_values(
            stage_summaries or previous.get("stage_summaries") or {},
            ctx=ctx,
        ),
        "models": _model_references(ctx),
        "counts": counts or previous.get("counts") or {},
        "quality": quality,
        "metrics": metrics if metrics is not None else previous.get("metrics"),
        "artifact_versions": ctx.get("artifact_versions") or {},
        "sources": sources or previous.get("sources") or {},
        "indexes": indexes or previous.get("indexes") or {},
    }
    normalized_payload = _json_payload(payload)
    write_json(summary_path, normalized_payload)
    return normalized_payload


def run_catalog_entry(
    summary: dict[str, Any],
    *,
    suite_id: str | None = None,
) -> dict[str, Any]:
    """Project a run summary into the lightweight catalog contract."""
    quality = summary.get("quality")
    if not isinstance(quality, dict):
        quality = {}
    return {
        "suite_id": summary.get("suite_id") or suite_id,
        "run_id": summary.get("run_id"),
        "status": summary.get("state"),
        "current_stage": summary.get("current_stage"),
        "started_at": summary.get("started_at"),
        "ended_at": summary.get("ended_at"),
        "updated_at": summary.get("updated_at"),
        "prompt_metrics": quality.get("prompt"),
        "scenario_metrics": quality.get("scenario"),
        "models": summary.get("models") or {},
        "counts": summary.get("counts") or {},
        "metrics": summary.get("metrics"),
    }


def write_run_catalog(
    suite_root: Path,
    run_summaries: list[dict[str, Any]],
    *,
    catalog_identity: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Atomically persist projected run metadata for one stable suite snapshot."""
    expected_identity = (
        suite_run_catalog_identity(suite_root)
        if catalog_identity is None
        else catalog_identity
    )
    if suite_run_catalog_identity(suite_root) != expected_identity:
        return None

    payload = _json_payload(
        {
            "schema_version": RUN_CATALOG_SCHEMA_VERSION,
            "suite_id": suite_root.name,
            "generated_at": _utc_now(),
            "run_catalog_identity": expected_identity,
            "items": [
                run_catalog_entry(summary, suite_id=suite_root.name)
                for summary in run_summaries
            ],
            "summary_sources": {
                str(summary.get("run_id")): (
                    summary.get("sources")
                    if isinstance(summary.get("sources"), dict)
                    else {}
                )
                for summary in run_summaries
                if isinstance(summary.get("run_id"), str)
            },
        }
    )
    write_json(suite_root / RUN_CATALOG_FILENAME, payload)

    if suite_run_catalog_identity(suite_root) != expected_identity:
        return None
    return payload


def write_suite_summary(
    ctx: dict[str, Any],
    *,
    rebuild_indexes: bool = True,
) -> dict[str, Any] | None:
    """Write one metadata-only suite catalog entry."""
    suite_root = _optional_path(ctx.get("suite_root"))
    if suite_root is None:
        return None
    suite_root.mkdir(parents=True, exist_ok=True)

    summary_path = suite_root / "suite_summary.json"
    previous = _load_optional_json(summary_path) or {}
    suite_meta = _load_optional_json(suite_root / "suite.json") or {}
    taxonomy_path = _optional_path(
        ctx.get("taxonomy_path") or suite_root / "taxonomy.json"
    )
    test_set_path = _optional_path(
        ctx.get("test_set_path") or suite_root / "test_set.jsonl"
    )

    taxonomy = (
        _load_optional_json(taxonomy_path)
        if taxonomy_path is not None and taxonomy_path.is_file()
        else None
    )
    categories = (
        taxonomy.get("behavior_categories")
        if isinstance(taxonomy, dict)
        else None
    )
    if not isinstance(categories, list):
        categories = []

    sources: dict[str, Any] = {}
    if taxonomy_path is not None and taxonomy_path.is_file():
        sources["taxonomy"] = _file_identity(
            taxonomy_path,
            suite_root=suite_root,
            run_root=None,
            ctx=ctx,
        )

    test_case_counts = previous.get("test_case_counts") or {
        "total": 0,
        "prompt": 0,
        "scenario": 0,
        "other": 0,
    }
    if test_set_path is not None and test_set_path.is_file():
        test_set_index = _load_or_build_jsonl_index(
            test_set_path,
            rebuild=rebuild_indexes,
        )
        if test_set_index is not None:
            test_case_counts = _index_counts(test_set_index)
            sources["test_set"] = {
                **_path_reference(
                    test_set_path,
                    suite_root=suite_root,
                    run_root=None,
                    ctx=ctx,
                ),
                **test_set_index["source"],
                "index_schema_version": test_set_index["schema_version"],
                "index": _path_reference(
                    jsonl_index_path(test_set_path),
                    suite_root=suite_root,
                    run_root=None,
                    ctx=ctx,
                ),
            }

    catalog_identity_before = suite_run_catalog_identity(suite_root)
    runs = _run_catalog_entries(suite_root)
    catalog_identity = suite_run_catalog_identity(suite_root)
    if catalog_identity_before == catalog_identity:
        write_run_catalog(
            suite_root,
            runs,
            catalog_identity=catalog_identity,
        )
    latest_run = max(
        runs,
        key=lambda item: str(
            item.get("ended_at")
            or item.get("updated_at")
            or item.get("started_at")
            or ""
        ),
        default=None,
    )
    has_results = any(
        int(((entry.get("counts") or {}).get("scores") or {}).get("total", 0))
        > 0
        for entry in runs
    )
    if has_results:
        status = "has_results"
    elif int(test_case_counts.get("total", 0)) > 0:
        status = "test_set_ready"
    elif taxonomy_path is not None and taxonomy_path.is_file():
        status = "systematized"
    else:
        status = "initialized"

    behavior_block = (
        taxonomy.get("behavior")
        if isinstance(taxonomy, dict)
        else None
    )
    taxonomy_behavior_name = (
        behavior_block.get("name")
        if isinstance(behavior_block, dict)
        else None
    )
    payload = {
        "schema_version": SUITE_SUMMARY_SCHEMA_VERSION,
        "suite_id": str(ctx.get("suite_id") or suite_root.name),
        "status": status,
        "behavior": {
            "name": (
                ctx.get("behavior_name")
                or taxonomy_behavior_name
                or suite_root.name
            ),
            "description": ctx.get("behavior") or "",
        },
        "behavior_category_count": len(categories),
        "test_case_counts": test_case_counts,
        "created_at": (
            suite_meta.get("created_at")
            or previous.get("created_at")
            or _utc_now()
        ),
        "updated_at": _utc_now(),
        "run_count": len(runs),
        "run_set_identity": suite_run_set_identity(suite_root),
        "run_catalog_identity": catalog_identity,
        "latest_run": (
            {
                "run_id": latest_run.get("run_id"),
                "state": latest_run.get("state"),
                "started_at": latest_run.get("started_at"),
                "ended_at": latest_run.get("ended_at"),
            }
            if latest_run is not None
            else None
        ),
        "artifact_versions": ctx.get("artifact_versions") or {},
        "sources": sources or previous.get("sources") or {},
    }
    normalized_payload = _json_payload(payload)
    write_json(summary_path, normalized_payload)
    return normalized_payload


def _ensure_jsonl_index(path: Path) -> dict[str, Any]:
    return _load_or_build_jsonl_index(path, rebuild=True) or {}


def _load_or_build_jsonl_index(
    path: Path,
    *,
    rebuild: bool,
) -> dict[str, Any] | None:
    try:
        return load_jsonl_index(path)
    except JsonlIndexError as exc:
        if (
            not rebuild
            or exc.code
            not in {
                JsonlIndexErrorCode.INVALID_INDEX,
                JsonlIndexErrorCode.STALE_INDEX,
            }
        ):
            return None
    try:
        scan = scan_jsonl(
            path,
            allow_trailing_partial=path.name
            in {"inference_set.jsonl", "scores.jsonl"},
        )
        return build_jsonl_index(path, scan=scan)
    except JsonlIndexError:
        return None


def _index_counts(index: dict[str, Any]) -> dict[str, int]:
    counts = {
        "total": 0,
        "prompt": 0,
        "scenario": 0,
        "other": 0,
    }
    for item in index.get("items", {}).values():
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        counts["total"] += 1
        if kind in {"prompt", "scenario"}:
            counts[kind] += 1
        else:
            counts["other"] += 1
    return counts


def _run_catalog_entries(suite_root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for child in sorted(suite_root.iterdir()) if suite_root.exists() else []:
        if not child.is_dir() or child.name == "artifacts":
            continue
        summary = _load_optional_json(child / "run_summary.json")
        if isinstance(summary, dict):
            entries.append(summary)
            continue
        manifest = _load_optional_json(child / "manifest.json")
        if not isinstance(manifest, dict):
            continue
        entries.append(
            {
                "run_id": child.name,
                "state": manifest.get("status") or "unknown",
                "started_at": manifest.get("started_at"),
                "ended_at": manifest.get("ended_at"),
                "counts": {},
            }
        )
    return entries


def _model_references(ctx: dict[str, Any]) -> dict[str, Any]:
    target = ctx.get("target")
    evaluation = ctx.get("evaluation")
    target_ref: dict[str, Any] | None = None
    if target is not None:
        model = getattr(target, "model", None)
        if model is not None:
            target_ref = {
                "kind": "model",
                "identifier": _sanitize_managed_string(
                    str(getattr(model, "name", model)),
                    ctx=ctx,
                ),
            }
        else:
            for kind in ("connector", "callable", "endpoint"):
                value = getattr(target, kind, None)
                if value:
                    target_ref = {
                        "kind": kind,
                        "identifier": _safe_target_identifier(
                            kind,
                            str(value),
                            ctx=ctx,
                        ),
                    }
                    break
    tester = getattr(evaluation, "tester", None) if evaluation is not None else None
    judge = getattr(evaluation, "judge", None) if evaluation is not None else None
    return {
        "target": target_ref,
        "tester": (
            _optional_sanitized_model_name(tester, ctx=ctx)
            if tester is not None
            else None
        ),
        "judge": (
            _optional_sanitized_model_name(judge, ctx=ctx)
            if judge is not None
            else None
        ),
    }


def _current_or_terminal_stage(manifest: dict[str, Any]) -> str | None:
    stages = manifest.get("stages")
    if not isinstance(stages, dict):
        return None
    for stage_name, stage_status in stages.items():
        if stage_status == "running":
            return str(stage_name)
    for stage_name, stage_status in reversed(list(stages.items())):
        if stage_status in {"completed", "failed", "cancelled"}:
            return str(stage_name)
    return None


def _file_identity(
    path: Path,
    *,
    suite_root: Path,
    run_root: Path | None,
    ctx: dict[str, Any],
) -> dict[str, Any]:
    stat_result = path.stat()
    return {
        **_path_reference(
            path,
            suite_root=suite_root,
            run_root=run_root,
            ctx=ctx,
        ),
        "name": path.name,
        "size_bytes": stat_result.st_size,
        "mtime_ns": stat_result.st_mtime_ns,
        "sha256": _file_sha256(path),
    }


def _path_reference(
    path: Path,
    *,
    suite_root: Path,
    run_root: Path | None,
    ctx: dict[str, Any],
) -> dict[str, str]:
    resolved = path.resolve()
    roots: list[tuple[str, Path]] = []
    if run_root is not None:
        roots.append(("run", run_root.resolve()))
    roots.append(("suite", suite_root.resolve()))
    path_policy = ctx.get("path_policy")
    if path_policy is not None:
        roots.append(("workspace", Path(path_policy.workspace_root).resolve()))
    for scope, root in roots:
        try:
            relative = resolved.relative_to(root)
        except ValueError:
            continue
        return {"scope": scope, "path": relative.as_posix()}
    return {"scope": "external"}


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _optional_path(value: Any) -> Path | None:
    if value is None or value == "":
        return None
    return Path(value)


def _under_run_root(ctx: dict[str, Any], filename: str) -> Path | None:
    run_root = _optional_path(ctx.get("run_root"))
    return run_root / filename if run_root is not None else None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_optional_json(path: Path) -> dict[str, Any] | None:
    try:
        return load_json(path)
    except (OSError, ValueError):
        return None


def _json_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize mapping keys exactly as they will appear in persisted JSON."""
    normalized = json.loads(json.dumps(payload, ensure_ascii=False))
    assert isinstance(normalized, dict)
    return normalized


def suite_run_catalog_identity(suite_root: Path) -> dict[str, Any]:
    """Return a cheap identity for the suite's run set and run metadata."""
    entries: list[dict[str, Any]] = []
    if suite_root.exists():
        for child in sorted(suite_root.iterdir()):
            if (
                not child.is_dir()
                or child.name == "artifacts"
                or child.name.startswith(".")
            ):
                continue
            files: dict[str, dict[str, int]] = {}
            for filename in (
                "run_summary.json",
                "manifest.json",
                "inference_set.jsonl",
                "scores.jsonl",
            ):
                path = child / filename
                if not path.is_file():
                    continue
                stat_result = path.stat()
                files[filename] = {
                    "size_bytes": stat_result.st_size,
                    "mtime_ns": stat_result.st_mtime_ns,
                }
            if files:
                entries.append({"run_id": child.name, "files": files})
    encoded = json.dumps(
        entries,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "run_count": len(entries),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


def suite_run_set_identity(suite_root: Path) -> dict[str, Any]:
    """Return a cheap identity that detects added or removed run directories."""
    run_ids = (
        sorted(
            child.name
            for child in suite_root.iterdir()
            if child.is_dir()
            and child.name != "artifacts"
            and not child.name.startswith(".")
            and any(
                (child / filename).exists()
                for filename in (
                    "run_summary.json",
                    "manifest.json",
                    "inference_set.jsonl",
                    "scores.jsonl",
                )
            )
        )
        if suite_root.exists()
        else []
    )
    encoded = json.dumps(
        run_ids,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "run_count": len(run_ids),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


def _safe_target_identifier(
    kind: str,
    value: str,
    *,
    ctx: dict[str, Any],
) -> str:
    if kind != "endpoint":
        return _sanitize_managed_string(value, ctx=ctx)
    try:
        parsed = urlsplit(value)
        if not parsed.scheme or not parsed.hostname:
            return "<configured endpoint>"
        host = parsed.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        if parsed.port is not None:
            host = f"{host}:{parsed.port}"
        return urlunsplit((parsed.scheme, host, "", "", ""))
    except ValueError:
        return "<configured endpoint>"


def _optional_sanitized_model_name(
    owner: Any,
    *,
    ctx: dict[str, Any],
) -> str | None:
    value = getattr(getattr(owner, "model", None), "name", None)
    if value is None:
        return None
    return _sanitize_managed_string(str(value), ctx=ctx)


def _sanitize_managed_values(value: Any, *, ctx: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _sanitize_managed_values(item, ctx=ctx)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_managed_values(item, ctx=ctx) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_managed_values(item, ctx=ctx) for item in value]
    if isinstance(value, str):
        return _sanitize_managed_string(value, ctx=ctx)
    return value


def _sanitize_managed_string(value: str, *, ctx: dict[str, Any]) -> str:
    path_policy = ctx.get("path_policy")
    if path_policy is None:
        return value
    roots = {
        Path(path_policy.workspace_root),
        Path(path_policy.config_root),
        Path(path_policy.artifacts_root),
        Path(path_policy.results_root),
    }
    sanitized = value
    for root in sorted(roots, key=lambda path: len(str(path)), reverse=True):
        for rendered in {str(root), root.as_posix()}:
            sanitized = sanitized.replace(rendered, ".")
    return sanitized
