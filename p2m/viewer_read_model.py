"""Build compact run-level viewer artifacts from canonical run outputs."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml

from p2m.core.io import write_json, row_failure_mode

VIEWER_READ_MODEL_SCHEMA_VERSION = 1
VIEWER_READ_MODEL_GENERATOR_VERSION = "viewer-read-model-v1"
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


def _kind_and_seed_id(row: dict[str, Any], *, path: Path) -> tuple[str, str]:
    kind = row.get("kind")
    seed_id = row.get("seed_id")
    if not isinstance(kind, str) or kind not in {"prompt", "scenario"}:
        raise ViewerReadModelBuildError(f"Missing or invalid kind in {path}")
    if not isinstance(seed_id, str) or not seed_id:
        raise ViewerReadModelBuildError(f"Missing or invalid seed_id in {path}")
    return kind, seed_id


def _viewer_index_key(kind: str, seed_id: str) -> str:
    return f"{kind}:{seed_id}"


def _put_unique_row(
    rows: dict[str, dict[str, Any]],
    *,
    kind: str,
    seed_id: str,
    row: dict[str, Any],
    path: Path,
) -> None:
    key = _viewer_index_key(kind, seed_id)
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
    factors = {
        key: factor
        for key, factor in value.items()
        if isinstance(key, str) and isinstance(factor, str)
    }
    return factors or None


def _event_views(event: dict[str, Any]) -> list[str]:
    raw_view = event.get("view")
    if isinstance(raw_view, list):
        return [view for view in raw_view if isinstance(view, str)]
    if isinstance(raw_view, str):
        return [raw_view]
    return []


def _materialize_target_messages(transcript_row: dict[str, Any]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    judge_turn = 0
    for event_index, event in enumerate(transcript_row.get("events", [])):
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

        if kind in {"add_message", "set_system_message"}:
            payload = _read_object(edit.get("message"))
            if payload is None:
                continue
            role = payload.get("role")
            content = payload.get("content")
            if not isinstance(role, str) or not isinstance(content, str):
                continue
            message_judge_turn = None if kind == "set_system_message" else judge_turn + 1
            if message_judge_turn is not None:
                judge_turn = message_judge_turn
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
                    "raw": raw,
                }
            )
            continue

        if kind != "tool_call":
            continue

        tool_name = edit.get("tool_name")
        if not isinstance(tool_name, str):
            continue
        judge_turn += 1
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
                "raw": raw,
            }
        )
    return messages


def _count_target_conversation_messages(transcript_row: dict[str, Any]) -> int:
    count = 0
    for event in transcript_row.get("events", []):
        if not isinstance(event, dict):
            continue
        if "target" not in _event_views(event):
            continue
        edit = _read_object(event.get("edit"))
        if edit is None:
            continue
        if edit.get("type") == "tool_call":
            count += 1
            continue
        payload = _read_object(edit.get("message"))
        if payload is None:
            continue
        if payload.get("role") != "system":
            count += 1
    return count


def _prompt_preview(transcript_row: dict[str, Any]) -> str:
    prompt = ""
    for message in _materialize_target_messages(transcript_row):
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
    transcript_rows: list[tuple[int, int, dict[str, Any]]],
) -> dict[str, Any]:
    items: dict[str, dict[str, Any]] = {}
    for offset, length, row in transcript_rows:
        transcript_path = run_dir / "transcripts.jsonl"
        kind, seed_id = _kind_and_seed_id(row, path=transcript_path)
        _put_unique_row(
            items,
            kind=kind,
            seed_id=seed_id,
            row={
                "kind": kind,
                "seed_id": seed_id,
                "offset": offset,
                "length": length,
            },
            path=transcript_path,
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
        kind, seed_id = _kind_and_seed_id(row, path=scores_path)
        _put_unique_row(
            items,
            kind=kind,
            seed_id=seed_id,
            row={
                "kind": kind,
                "seed_id": seed_id,
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
    seeds_path = suite_dir / "seeds.jsonl"
    manifest_path = run_dir / "manifest.json"
    config_path = run_dir / "config.yaml"
    transcripts_path = run_dir / "transcripts.jsonl"
    scores_path = run_dir / "scores.jsonl"
    viewer_dir = run_dir / VIEWER_CACHE_DIR
    viewer_dir.mkdir(parents=True, exist_ok=True)
    _remove_legacy_viewer_files(run_dir)

    transcript_rows = _iter_jsonl_with_offsets(transcripts_path)
    transcript_index_meta = _write_transcript_index(
        run_dir=run_dir,
        relative_root=relative_root,
        transcript_rows=transcript_rows,
    )

    if not scores_path.exists():
        return {
            "mode": "transcript_only",
            "run_dir": str(run_dir),
            "built_files": [_viewer_relative_name(VIEWER_TRANSCRIPT_INDEX_FILE)],
        }

    config = _load_yaml_file(config_path)
    if manifest_path.exists():
        _load_json_file(manifest_path)
    runtime_mode = _runtime_mode(config)

    transcript_by_seed: dict[tuple[str, str], dict[str, Any]] = {}
    for _, _, row in transcript_rows:
        kind, seed_id = _kind_and_seed_id(row, path=transcripts_path)
        key = (kind, seed_id)
        if key in transcript_by_seed:
            raise ViewerReadModelBuildError(f"Duplicate {kind}:{seed_id} row in {transcripts_path}")
        transcript_by_seed[key] = row

    seeds_by_seed: dict[tuple[str, str], dict[str, Any]] = {}
    if seeds_path.exists():
        for _, _, row in _iter_jsonl_with_offsets(seeds_path):
            kind, seed_id = _kind_and_seed_id(row, path=seeds_path)
            key = (kind, seed_id)
            if key in seeds_by_seed:
                raise ViewerReadModelBuildError(f"Duplicate {kind}:{seed_id} row in {seeds_path}")
            seeds_by_seed[key] = row

    score_rows = _iter_jsonl_with_offsets(scores_path)
    prompt_rows: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []
    for _, _, row in score_rows:
        kind, seed_id = _kind_and_seed_id(row, path=scores_path)
        transcript_row = transcript_by_seed.get((kind, seed_id))
        if transcript_row is None:
            raise ViewerReadModelBuildError(
                f"Missing transcript row for {kind}:{seed_id} while building {run_dir}"
            )
        seed_row = seeds_by_seed.get((kind, seed_id))
        factors = (
            _read_factors(seed_row.get("factors") if seed_row is not None else None)
            or _read_factors(row.get("factors"))
            or _read_factors(transcript_row.get("factors"))
        )

        if kind == "prompt":
            prompt_row = {
                "seed_id": seed_id,
                "prompt": _prompt_preview(transcript_row),
                "response": "",
                "spec": row.get("spec"),
                "failure_mode": row_failure_mode(row),
                "run_id": run_dir.name,
                "judge_model": row.get("judge_model"),
                "target": row.get("target") or transcript_row.get("target"),
                "verdict": _summary_verdict(row.get("verdict")),
                "judge_status": row.get("judge_status"),
                "judge_error": row.get("judge_error"),
                "target_runtime_mode": runtime_mode,
                "multi_judge": _summary_multi_judge(row.get("multi_judge")),
            }
            if factors:
                prompt_row["factors"] = factors
            prompt_rows.append(prompt_row)
            continue

        audit_row = {
            "seed_id": seed_id,
            "spec": row.get("spec", ""),
            "failure_mode": row_failure_mode(row),
            "judge_model": row.get("judge_model", ""),
            "target": row.get("target") or transcript_row.get("target"),
            "tester_model": row.get("tester_model") or transcript_row.get("tester_model"),
            "verdict": _summary_verdict(row.get("verdict")),
            "judge_status": row.get("judge_status"),
            "judge_error": row.get("judge_error"),
            "target_runtime_mode": runtime_mode,
            "metadata": {
                "turns_count": _count_target_conversation_messages(transcript_row),
                "stop_reason": transcript_row.get("stop_reason", ""),
            },
            "multi_judge": _summary_multi_judge(row.get("multi_judge")),
        }
        if factors:
            audit_row["factors"] = factors
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
            "transcripts.jsonl": _file_metadata(transcripts_path, relative_to=relative_root),
            "scores.jsonl": _file_metadata(scores_path, relative_to=relative_root),
        },
        "derived_files": derived_files,
    }
    if seeds_path.exists():
        manifest_payload["source_files"]["seeds.jsonl"] = _file_metadata(
            seeds_path, relative_to=relative_root
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
