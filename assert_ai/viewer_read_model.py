# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Build compact run-level viewer artifacts from canonical run outputs."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import yaml

from assert_ai.core.io import write_json, row_behavior

log = logging.getLogger(__name__)

VIEWER_READ_MODEL_SCHEMA_VERSION = 4
VIEWER_READ_MODEL_GENERATOR_VERSION = "viewer-read-model-v3"
VIEWER_CACHE_DIR = ".viewer"

VIEWER_RUN_MANIFEST_FILE = "viewer_run_manifest.json"
VIEWER_PROMPT_ROWS_FILE = "viewer_prompt_rows.json"
VIEWER_AUDIT_ROWS_FILE = "viewer_audit_rows.json"
VIEWER_TRANSCRIPT_INDEX_FILE = "viewer_transcript_index.json"
VIEWER_SCORE_INDEX_FILE = "viewer_score_index.json"


class ViewerReadModelBuildError(RuntimeError):
    """Raised when canonical artifacts cannot be turned into a viewer read model."""


def _viewer_cache_path(run_dir: Path, filename: str) -> Path:
    return run_dir / VIEWER_CACHE_DIR / filename


def _viewer_output_names() -> tuple[str, ...]:
    return (
        VIEWER_RUN_MANIFEST_FILE,
        VIEWER_PROMPT_ROWS_FILE,
        VIEWER_AUDIT_ROWS_FILE,
        VIEWER_TRANSCRIPT_INDEX_FILE,
        VIEWER_SCORE_INDEX_FILE,
    )


def _viewer_relative_name(filename: str) -> str:
    return str(Path(VIEWER_CACHE_DIR) / filename)


def _remove_legacy_viewer_files(run_dir: Path) -> None:
    for filename in _viewer_output_names():
        legacy_path = run_dir / filename
        legacy_path.unlink(missing_ok=True)


def _manifest_relative_path(base_dir: Path, raw_path: str) -> Path | None:
    """Resolve a manifest-provided relative path under ``base_dir``.

    Rejects any path with parent-directory (``..``) segments so that a
    tampered or corrupted ``manifest.json`` cannot redirect viewer reads
    outside the suite directory. Also rejects paths that normalize to no
    segments (e.g. ``"."`` or ``"./"``) so the loader does not try to read
    the suite directory itself as a JSONL file.
    """

    parts = [part for part in raw_path.replace("\\", "/").split("/") if part and part != "."]
    if not parts:
        log.warning(
            "Refusing manifest path that normalizes to no segments: %r",
            raw_path,
        )
        return None
    if any(part == ".." for part in parts):
        log.warning(
            "Refusing manifest path with parent segments: %r",
            raw_path,
        )
        return None
    return base_dir.joinpath(*parts)


def _test_set_artifact_path(suite_dir: Path, manifest: dict[str, Any] | None) -> Path:
    """Return the test set artifact path selected by a run, with legacy fallback."""

    artifacts = manifest.get("artifact_versions") if isinstance(manifest, dict) else None
    if isinstance(artifacts, dict):
        test_set = artifacts.get("test_set")
        if isinstance(test_set, dict):
            raw_path = test_set.get("path") or test_set.get("relative_path")
            if isinstance(raw_path, str) and raw_path:
                if Path(raw_path).is_absolute():
                    log.warning(
                        "Refusing absolute manifest artifact path: %r",
                        raw_path,
                    )
                else:
                    resolved = _manifest_relative_path(suite_dir, raw_path)
                    if resolved is not None:
                        return resolved
    return suite_dir / "test_set.jsonl"


def _load_json_file(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ViewerReadModelBuildError(f"Missing JSON artifact: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ViewerReadModelBuildError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ViewerReadModelBuildError(f"Expected JSON object in {path}")
    return payload


def _load_yaml_file(path: Path) -> dict[str, Any] | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        payload = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ViewerReadModelBuildError(f"Invalid YAML in {path}: {exc}") from exc
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise ViewerReadModelBuildError(f"Expected YAML mapping in {path}")
    return payload


def _file_metadata(path: Path, *, relative_to: Path) -> dict[str, Any]:
    try:
        stat = path.stat()
    except OSError as exc:
        raise ViewerReadModelBuildError(f"Missing artifact required for viewer rebuild: {path}") from exc
    return {
        "path": os.path.relpath(path, relative_to),
        "size_bytes": stat.st_size,
        "mtime_ms": stat.st_mtime_ns // 1_000_000,
    }


def _iter_jsonl_with_offsets(path: Path) -> list[tuple[int, int, dict[str, Any]]]:
    if not path.exists():
        raise ViewerReadModelBuildError(f"Missing JSONL artifact: {path}")

    rows: list[tuple[int, int, dict[str, Any]]] = []
    offset = 0
    with path.open("rb") as handle:
        for line_number, raw_line in enumerate(handle, 1):
            length = len(raw_line)
            stripped = raw_line.strip()
            if not stripped:
                offset += length
                continue
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ViewerReadModelBuildError(
                    f"Invalid JSONL in {path} on line {line_number}: {exc}"
                ) from exc
            if not isinstance(row, dict):
                raise ViewerReadModelBuildError(f"Expected JSON object in {path} on line {line_number}")
            rows.append((offset, length, row))
            offset += length
    return rows


def _kind_and_test_case_id(row: dict[str, Any], *, path: Path) -> tuple[str, str]:
    kind = row.get("type")
    test_case_id = row.get("test_case_id")
    if not isinstance(kind, str) or kind not in {"prompt", "scenario"}:
        _raise_schema_hint(row, path, field="type", expected='"prompt" or "scenario"')
    if not isinstance(test_case_id, str) or not test_case_id:
        _raise_schema_hint(row, path, field="test_case_id", expected="a non-empty string")
    return kind, test_case_id


def _raise_schema_hint(
    row: dict[str, Any], path: Path, *, field: str, expected: str
) -> None:
    """Raise a *ViewerReadModelBuildError* with actionable guidance.

    When cached artifacts are left over from a previous run that used a
    different schema, the error message tells the user to delete the stale
    run directory and re-run the pipeline — without exposing internal
    migration details.
    """
    actual_keys = ", ".join(sorted(row.keys())[:10])
    raise ViewerReadModelBuildError(
        f'{path.name}: expected field "{field}" ({expected}) but the row '
        f"contains [{actual_keys}]. The file appears to use an outdated "
        f"format that is incompatible with the current version of ASSERT.\n"
        f"  To fix: delete the run directory\n"
        f"    rm -rf {path.parent}\n"
        f"  and re-run the pipeline so all artifacts are regenerated."
    )


def _viewer_index_key(kind: str, test_case_id: str) -> str:
    return f"{kind}:{test_case_id}"


def _put_unique_row(
    rows: dict[str, dict[str, Any]],
    *,
    kind: str,
    test_case_id: str,
    row: dict[str, Any],
    path: Path,
) -> None:
    key = _viewer_index_key(kind, test_case_id)
    if key in rows:
        raise ViewerReadModelBuildError(f"Duplicate {key} row in {path}")
    rows[key] = row


def _runtime_mode(config: dict[str, Any] | None) -> str | None:
    if not isinstance(config, dict):
        return None
    pipeline = config.get("pipeline")
    inference = pipeline.get("inference") if isinstance(pipeline, dict) else None
    target = inference.get("target") if isinstance(inference, dict) else None
    if not isinstance(target, dict):
        return None

    connector = target.get("connector")
    if isinstance(connector, str) and connector:
        return "external"

    tools = target.get("tools")
    if isinstance(tools, dict):
        module = tools.get("module")
        toolset = tools.get("toolset")
        if isinstance(module, str) and module:
            return "tool_module"
        if isinstance(toolset, str) and toolset:
            return "simulated"

    target_model = target.get("model")
    if isinstance(target_model, dict):
        name = target_model.get("name")
        if isinstance(name, str) and name:
            return "chat"
    return None


def _read_object(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _read_factors(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict) or not value:
        return None
    dimensions = {
        key: dimension
        for key, dimension in value.items()
        if isinstance(key, str) and isinstance(dimension, str)
    }
    return dimensions or None


def _event_views(event: dict[str, Any]) -> list[str]:
    raw_view = event.get("view")
    if isinstance(raw_view, list):
        return [view for view in raw_view if isinstance(view, str)]
    if isinstance(raw_view, str):
        return [raw_view]
    return []


def _agent_label_from_raw(raw: dict[str, Any] | None) -> str | None:
    if not isinstance(raw, dict):
        return None
    node = raw.get("_node")
    if isinstance(node, str):
        node = node.strip()
        return node or None
    return None


def _materialize_target_messages(inference_row: dict[str, Any]) -> list[dict[str, Any]]:
    """Materialize transcript events into viewer messages with turn labels.

    Turn semantics: only the tester (user) and the target (assistant) emit
    "turns". A target turn = one block of consecutive assistant emissions
    *plus* any tool calls or tool results issued during that block. Tool
    messages inherit the surrounding assistant turn — they never get their
    own turn number, but they DO carry the assistant's turn label so the
    viewer can group them under the right turn.

    System messages and ``set_system_message`` edits never get a turn label.
    """
    messages: list[dict[str, Any]] = []
    judge_turn = 0
    last_principal_role: str | None = None
    for event_index, event in enumerate(inference_row.get("events", [])):
        if not isinstance(event, dict):
            continue
        if "target" not in _event_views(event):
            continue
        edit = _read_object(event.get("edit"))
        if edit is None:
            continue

        kind = edit.get("type")
        raw = _read_object(event.get("raw"))
        message_id = f"event:{event_index}"
        agent = _agent_label_from_raw(raw)

        if kind in {"add_message", "set_system_message"}:
            payload = _read_object(edit.get("message"))
            if payload is None:
                continue
            role = payload.get("role")
            content = payload.get("content")
            if not isinstance(role, str) or not isinstance(content, str):
                continue

            message_judge_turn: int | None
            if kind == "set_system_message" or role == "system":
                message_judge_turn = None
            elif role == "user":
                judge_turn += 1
                message_judge_turn = judge_turn
                last_principal_role = "user"
            elif role in {"assistant", "tool"}:
                if last_principal_role != "assistant":
                    judge_turn += 1
                message_judge_turn = judge_turn
                last_principal_role = "assistant"
            else:
                message_judge_turn = None

            messages.append(
                {
                    "id": message_id,
                    "role": role,
                    "content": content,
                    "type": "set_system_message" if kind == "set_system_message" else "message",
                    "judgeTurn": message_judge_turn,
                    "tool_calls": payload.get("tool_calls"),
                    "tool_call_id": payload.get("tool_call_id"),
                    "function": payload.get("function"),
                    "arguments": payload.get("arguments"),
                    "agent": agent if role == "assistant" else None,
                    "raw": raw,
                }
            )
            continue

        if kind != "tool_call":
            continue

        tool_name = edit.get("tool_name")
        if not isinstance(tool_name, str):
            continue
        if last_principal_role != "assistant":
            judge_turn += 1
            last_principal_role = "assistant"
        messages.append(
            {
                "id": message_id,
                "role": "tool",
                "content": "",
                "type": "tool_call",
                "judgeTurn": judge_turn,
                "tool_call_id": edit.get("tool_call_id"),
                "function": tool_name,
                "arguments": edit.get("tool_args") if isinstance(edit.get("tool_args"), dict) else {},
                "agent": None,
                "raw": raw,
            }
        )
    return messages


def _count_target_conversation_messages(inference_row: dict[str, Any]) -> int:
    """Return the number of target turns (one per tester message + one per
    consecutive assistant block). Mirrors :func:`_materialize_target_messages`.
    """
    messages = _materialize_target_messages(inference_row)
    turns: set[int] = set()
    for message in messages:
        turn = message.get("judgeTurn")
        if isinstance(turn, int):
            turns.add(turn)
    return len(turns)


def _prompt_preview(inference_row: dict[str, Any]) -> str:
    prompt = ""
    for message in _materialize_target_messages(inference_row):
        if message.get("role") == "user":
            prompt = str(message.get("content") or "")
            break
    prompt = prompt.strip()
    if len(prompt) <= 240:
        return prompt
    return f"{prompt[:237].rstrip()}..."


def _summary_verdict(raw_verdict: Any) -> dict[str, Any] | None:
    if not isinstance(raw_verdict, dict):
        return None

    summary: dict[str, Any] = {}
    dimensions = raw_verdict.get("dimensions")
    if isinstance(dimensions, dict):
        summary["dimensions"] = {
            key: value
            for key, value in dimensions.items()
            if isinstance(key, str) and isinstance(value, bool)
        }
    raw_nj = raw_verdict.get("node_judgments")
    if isinstance(raw_nj, list):
        node_judgments: list[dict[str, Any]] = []
        for node in raw_nj:
            if not isinstance(node, dict) or node.get("relevant") is not True:
                continue
            name = node.get("node_name")
            violated = node.get("violated")
            node_judgments.append({
                "node_index": node.get("node_index"),
                "node_name": name if isinstance(name, str) else "",
                "relevant": True,
                "violated": violated if isinstance(violated, bool) else None,
            })
        summary["node_judgments"] = node_judgments
    error = raw_verdict.get("error")
    if isinstance(error, str) and error:
        summary["error"] = error
    return summary


def _summary_multi_judge(raw_multi_judge: Any) -> dict[str, Any] | None:
    if not isinstance(raw_multi_judge, dict):
        return None

    summary: dict[str, Any] = {
        "n": int(raw_multi_judge.get("n") or 0),
        "n_failed": int(raw_multi_judge.get("n_failed") or 0),
        "votes": raw_multi_judge.get("votes") if isinstance(raw_multi_judge.get("votes"), dict) else {},
        "means": raw_multi_judge.get("means") if isinstance(raw_multi_judge.get("means"), dict) else {},
        "agreement": float(raw_multi_judge.get("agreement") or 0),
        "justifications": [],
    }
    representative_index = raw_multi_judge.get("representative_index")
    if isinstance(representative_index, int):
        summary["representative_index"] = representative_index
    return summary


def _write_transcript_index(
    *,
    run_dir: Path,
    relative_root: Path,
    inference_rows: list[tuple[int, int, dict[str, Any]]],
) -> dict[str, Any]:
    items: dict[str, dict[str, Any]] = {}
    for offset, length, row in inference_rows:
        inference_set_path = run_dir / "inference_set.jsonl"
        kind, test_case_id = _kind_and_test_case_id(row, path=inference_set_path)
        _put_unique_row(
            items,
            kind=kind,
            test_case_id=test_case_id,
            row={
                "type": kind,
                "test_case_id": test_case_id,
                "offset": offset,
                "length": length,
            },
            path=inference_set_path,
        )

    out_path = _viewer_cache_path(run_dir, VIEWER_TRANSCRIPT_INDEX_FILE)
    write_json(out_path, {"items": items})
    return _file_metadata(out_path, relative_to=relative_root)


def _write_score_index(
    *,
    run_dir: Path,
    relative_root: Path,
    score_rows: list[tuple[int, int, dict[str, Any]]],
) -> dict[str, Any]:
    items: dict[str, dict[str, Any]] = {}
    for offset, length, row in score_rows:
        scores_path = run_dir / "scores.jsonl"
        kind, test_case_id = _kind_and_test_case_id(row, path=scores_path)
        _put_unique_row(
            items,
            kind=kind,
            test_case_id=test_case_id,
            row={
                "type": kind,
                "test_case_id": test_case_id,
                "offset": offset,
                "length": length,
            },
            path=scores_path,
        )

    out_path = _viewer_cache_path(run_dir, VIEWER_SCORE_INDEX_FILE)
    write_json(out_path, {"items": items})
    return _file_metadata(out_path, relative_to=relative_root)


def build_run_viewer_artifacts(run_dir: Path, *, suite_dir: Path | None = None) -> dict[str, Any]:
    """Build transcript index and, when scores exist, the full run-level viewer read model."""

    run_dir = run_dir.resolve()
    suite_dir = (suite_dir or run_dir.parent).resolve()
    relative_root = run_dir
    manifest_path = run_dir / "manifest.json"
    manifest = _load_json_file(manifest_path) if manifest_path.exists() else None
    test_set_path = _test_set_artifact_path(suite_dir, manifest)
    config_path = run_dir / "config.yaml"
    inference_set_path = run_dir / "inference_set.jsonl"
    scores_path = run_dir / "scores.jsonl"
    viewer_dir = run_dir / VIEWER_CACHE_DIR
    viewer_dir.mkdir(parents=True, exist_ok=True)
    _remove_legacy_viewer_files(run_dir)

    inference_rows = _iter_jsonl_with_offsets(inference_set_path)
    transcript_index_meta = _write_transcript_index(
        run_dir=run_dir,
        relative_root=relative_root,
        inference_rows=inference_rows,
    )

    if not scores_path.exists():
        return {
            "mode": "transcript_only",
            "run_dir": str(run_dir),
            "built_files": [_viewer_relative_name(VIEWER_TRANSCRIPT_INDEX_FILE)],
        }

    config = _load_yaml_file(config_path)
    runtime_mode = _runtime_mode(config)

    inference_by_test_case: dict[tuple[str, str], dict[str, Any]] = {}
    for _, _, row in inference_rows:
        kind, test_case_id = _kind_and_test_case_id(row, path=inference_set_path)
        key = (kind, test_case_id)
        if key in inference_by_test_case:
            raise ViewerReadModelBuildError(f"Duplicate {kind}:{test_case_id} row in {inference_set_path}")
        inference_by_test_case[key] = row

    test_cases_by_id: dict[tuple[str, str], dict[str, Any]] = {}
    if test_set_path.exists():
        for _, _, row in _iter_jsonl_with_offsets(test_set_path):
            kind, test_case_id = _kind_and_test_case_id(row, path=test_set_path)
            key = (kind, test_case_id)
            if key in test_cases_by_id:
                raise ViewerReadModelBuildError(f"Duplicate {kind}:{test_case_id} row in {test_set_path}")
            test_cases_by_id[key] = row

    score_rows = _iter_jsonl_with_offsets(scores_path)
    prompt_rows: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []
    for _, _, row in score_rows:
        kind, test_case_id = _kind_and_test_case_id(row, path=scores_path)
        inference_row = inference_by_test_case.get((kind, test_case_id))
        if inference_row is None:
            log.warning(
                "Stale scores.jsonl: %s:%s has no matching inference row in %s — "
                "skipping score rows and falling back to transcript-only mode",
                kind, test_case_id, run_dir,
            )
            return {
                "mode": "transcript_only",
                "run_dir": str(run_dir),
                "built_files": [_viewer_relative_name(VIEWER_TRANSCRIPT_INDEX_FILE)],
            }
        test_case_row = test_cases_by_id.get((kind, test_case_id))
        dimensions = (
            _read_factors(test_case_row.get("dimensions") if test_case_row is not None else None)
            or _read_factors(row.get("dimensions"))
            or _read_factors(inference_row.get("dimensions"))
        )

        if kind == "prompt":
            prompt_row = {
                "test_case_id": test_case_id,
                "prompt": _prompt_preview(inference_row),
                "response": "",
                "behavior": row.get("behavior"),
                "behavior": row_behavior(row),
                "run_id": run_dir.name,
                "judge_model": row.get("judge_model"),
                "target": row.get("target") or inference_row.get("target"),
                "verdict": _summary_verdict(row.get("verdict")),
                "judge_status": row.get("judge_status"),
                "judge_error": row.get("judge_error"),
                "target_runtime_mode": runtime_mode,
                "multi_judge": _summary_multi_judge(row.get("multi_judge")),
            }
            if dimensions:
                prompt_row["dimensions"] = dimensions
            prompt_rows.append(prompt_row)
            continue

        audit_row = {
            "test_case_id": test_case_id,
            "behavior": row.get("behavior", ""),
            "behavior": row_behavior(row),
            "judge_model": row.get("judge_model", ""),
            "target": row.get("target") or inference_row.get("target"),
            "tester_model": row.get("tester_model") or inference_row.get("tester_model"),
            "verdict": _summary_verdict(row.get("verdict")),
            "judge_status": row.get("judge_status"),
            "judge_error": row.get("judge_error"),
            "target_runtime_mode": runtime_mode,
            "metadata": {
                "turns_count": _count_target_conversation_messages(inference_row),
                "stop_reason": inference_row.get("stop_reason", ""),
            },
            "multi_judge": _summary_multi_judge(row.get("multi_judge")),
        }
        if dimensions:
            audit_row["dimensions"] = dimensions
        audit_rows.append(audit_row)

    prompt_rows_path = _viewer_cache_path(run_dir, VIEWER_PROMPT_ROWS_FILE)
    audit_rows_path = _viewer_cache_path(run_dir, VIEWER_AUDIT_ROWS_FILE)
    write_json(prompt_rows_path, prompt_rows)
    write_json(audit_rows_path, audit_rows)
    score_index_meta = _write_score_index(
        run_dir=run_dir,
        relative_root=relative_root,
        score_rows=score_rows,
    )

    derived_files = {
        VIEWER_PROMPT_ROWS_FILE: _file_metadata(prompt_rows_path, relative_to=relative_root),
        VIEWER_AUDIT_ROWS_FILE: _file_metadata(audit_rows_path, relative_to=relative_root),
        VIEWER_TRANSCRIPT_INDEX_FILE: transcript_index_meta,
        VIEWER_SCORE_INDEX_FILE: score_index_meta,
    }

    manifest_payload = {
        "schema_version": VIEWER_READ_MODEL_SCHEMA_VERSION,
        "generator_version": VIEWER_READ_MODEL_GENERATOR_VERSION,
        "suite_id": suite_dir.name,
        "run_id": run_dir.name,
        "source_files": {
            "inference_set.jsonl": _file_metadata(inference_set_path, relative_to=relative_root),
            "scores.jsonl": _file_metadata(scores_path, relative_to=relative_root),
        },
        "derived_files": derived_files,
    }
    if test_set_path.exists():
        manifest_payload["source_files"]["test_set.jsonl"] = _file_metadata(
            test_set_path, relative_to=relative_root
        )
    if manifest_path.exists():
        manifest_payload["source_files"]["manifest.json"] = _file_metadata(
            manifest_path, relative_to=relative_root
        )
    if config_path.exists():
        manifest_payload["source_files"]["config.yaml"] = _file_metadata(
            config_path, relative_to=relative_root
        )
    manifest_out_path = _viewer_cache_path(run_dir, VIEWER_RUN_MANIFEST_FILE)
    write_json(manifest_out_path, manifest_payload)

    return {
        "mode": "full",
        "run_dir": str(run_dir),
        "built_files": [
            _viewer_relative_name(VIEWER_RUN_MANIFEST_FILE),
            _viewer_relative_name(VIEWER_PROMPT_ROWS_FILE),
            _viewer_relative_name(VIEWER_AUDIT_ROWS_FILE),
            _viewer_relative_name(VIEWER_TRANSCRIPT_INDEX_FILE),
            _viewer_relative_name(VIEWER_SCORE_INDEX_FILE),
        ],
    }
