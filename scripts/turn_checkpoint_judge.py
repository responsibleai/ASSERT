"""Judge transcript prefixes at fixed inference-turn checkpoints and plot flagged rate."""

from __future__ import annotations

import argparse
import asyncio
import html
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from p2m.core.config_model import (
    DEFAULT_JUDGE_MAX_TOKENS,
    DEFAULT_JUDGE_TEMPERATURE,
    DEFAULT_INFERENCE_CONCURRENCY,
)
from p2m.core.io import (
    append_jsonl_row,
    load_jsonl,
    permissible_by_behavior,
    policy_permissible,
    resolve_path,
    write_json,
    write_jsonl,
    row_behavior,
    row_factors,
)
from p2m.core.judge import (
    build_judge_contract,
    get_verdict_dimension,
    infer_judge_status,
    is_valid_event_flag,
    run_transcript_judge,
)
from p2m.core.transcript import AddMessageEdit, Transcript, TranscriptEvent, TranscriptMetadata
from p2m.stages.judge import JUDGE_SYSTEM_PROMPT


@dataclass(frozen=True)
class CheckpointJudgeConfig:
    judge_model: str
    judge_temperature: float | None
    judge_max_tokens: int | None
    judge_n: int
    judge_dimensions: list[dict[str, Any]]
    concurrency: int


@dataclass(frozen=True)
class CheckpointTask:
    output_index: int
    transcript_row: dict[str, Any]
    checkpoint_turn: int
    final_transcript_turns: int
    transcript: Transcript


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Judge transcript prefixes at fixed inference-turn checkpoints.",
    )
    parser.add_argument(
        "--run-dir",
        required=True,
        help="Completed run directory containing transcripts.jsonl and config.yaml.",
    )
    parser.add_argument(
        "--checkpoint-step",
        type=int,
        default=5,
        help="Checkpoint interval in inference turns.",
    )
    parser.add_argument(
        "--dimension",
        default="policy_violation",
        help="Bad-event dimension to aggregate and plot.",
    )
    parser.add_argument(
        "--out-dir",
        help="Output directory. Defaults to <run-dir>/checkpoint_judge.",
    )
    parser.add_argument(
        "--judge-model",
        help="Override the judge model from the saved run config.",
    )
    parser.add_argument(
        "--judge-dimensions",
        nargs="*",
        help="Override extra judge dimensions from the saved run config.",
    )
    parser.add_argument(
        "--judge-n",
        type=int,
        help="Override the number of judge attempts from the saved run config.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        help="Override checkpoint judging concurrency.",
    )
    return parser.parse_args(argv)


def _require_positive_int(value: Any, *, field_name: str) -> int:
    if not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected YAML mapping at {path}")
    return data


def load_checkpoint_judge_config(
    config_path: Path,
    *,
    judge_model_override: str | None = None,
    judge_dimensions_override: list[dict[str, Any] | str] | None = None,
    judge_n_override: int | None = None,
    concurrency_override: int | None = None,
) -> CheckpointJudgeConfig:
    raw = _load_yaml_mapping(config_path)
    pipeline_raw = raw.get("pipeline")
    if not isinstance(pipeline_raw, dict):
        raise ValueError(f"Run config missing pipeline mapping: {config_path}")

    judge_stage_raw = pipeline_raw.get("judge")
    if not isinstance(judge_stage_raw, dict):
        raise ValueError(f"Run config missing pipeline.judge mapping: {config_path}")

    judge_model = judge_model_override
    if judge_model is None:
        model_raw = judge_stage_raw.get("model")
        if not isinstance(model_raw, dict):
            raise ValueError(f"Run config missing pipeline.judge.model mapping: {config_path}")
        judge_model = str(model_raw.get("name") or "").strip()
        if not judge_model:
            raise ValueError(f"Run config missing pipeline.judge.model.name: {config_path}")

    model_settings = judge_stage_raw.get("model")
    if isinstance(model_settings, dict):
        judge_temperature = model_settings.get("temperature", DEFAULT_JUDGE_TEMPERATURE)
        judge_max_tokens = model_settings.get("max_tokens", DEFAULT_JUDGE_MAX_TOKENS)
    else:
        judge_temperature = DEFAULT_JUDGE_TEMPERATURE
        judge_max_tokens = DEFAULT_JUDGE_MAX_TOKENS

    judge_n = judge_n_override if judge_n_override is not None else int(judge_stage_raw.get("n") or 1)
    if judge_n <= 0:
        raise ValueError("judge_n must be > 0")

    if judge_dimensions_override is None:
        raw_dimensions = judge_stage_raw.get("dimensions") or {}
        if not isinstance(raw_dimensions, dict):
            raise ValueError(f"pipeline.judge.dimensions must be a mapping: {config_path}")
        judge_dimensions = [
            {"name": str(name), **dimension}
            for name, dimension in raw_dimensions.items()
        ]
    else:
        judge_dimensions = [
            value
            if isinstance(value, dict)
            else {
                "name": str(value),
                "description": f"{value} description",
                "rubric": f"true = {value}; false = not {value}",
            }
            for value in judge_dimensions_override
        ]

    inference_stage_raw = pipeline_raw.get("inference")
    inference_concurrency = DEFAULT_INFERENCE_CONCURRENCY
    if isinstance(inference_stage_raw, dict) and inference_stage_raw.get("concurrency") is not None:
        inference_concurrency = _require_positive_int(
            int(inference_stage_raw["concurrency"]),
            field_name="pipeline.inference.concurrency",
        )
    concurrency = concurrency_override if concurrency_override is not None else inference_concurrency
    if concurrency <= 0:
        raise ValueError("concurrency must be > 0")

    return CheckpointJudgeConfig(
        judge_model=judge_model,
        judge_temperature=judge_temperature,
        judge_max_tokens=judge_max_tokens,
        judge_n=judge_n,
        judge_dimensions=judge_dimensions,
        concurrency=concurrency,
    )


def transcript_from_row(row: dict[str, Any]) -> Transcript:
    return Transcript(
        metadata=TranscriptMetadata(
            kind=str(row.get("type") or ""),
            test_case_id=str(row.get("test_case_id") or ""),
            behavior=str(row.get("behavior") or ""),
            target=str(row.get("target") or ""),
            tester_model=str(row.get("tester_model") or ""),
            dimensions=row_factors(row),
        ),
        events=[
            TranscriptEvent.model_validate(event)
            for event in row.get("events", [])
            if isinstance(event, dict)
        ],
        stop_reason=str(row.get("stop_reason") or "") or None,
    )


def _event_views(event: TranscriptEvent) -> list[str]:
    if isinstance(event.view, list):
        return [str(value) for value in event.view]
    return [str(event.view)]


def is_inference_turn_start(event: TranscriptEvent) -> bool:
    return (
        "target" in _event_views(event)
        and event.actor == "tester"
        and isinstance(event.edit, AddMessageEdit)
        and event.edit.message.role == "user"
    )


def count_inference_turns(transcript: Transcript) -> int:
    return sum(1 for event in transcript.events if is_inference_turn_start(event))


def checkpoint_turns(final_turns: int, checkpoint_step: int) -> list[int]:
    if checkpoint_step <= 0:
        raise ValueError("checkpoint_step must be > 0")
    return list(range(checkpoint_step, final_turns + 1, checkpoint_step))


def slice_transcript_at_turn(transcript: Transcript, checkpoint_turn: int) -> Transcript:
    if checkpoint_turn <= 0:
        raise ValueError("checkpoint_turn must be > 0")

    retained_events: list[TranscriptEvent] = []
    seen_turns = 0

    for event in transcript.events:
        if is_inference_turn_start(event):
            if seen_turns >= checkpoint_turn:
                break
            seen_turns += 1
        retained_events.append(event.model_copy(deep=True))

    if seen_turns < checkpoint_turn:
        raise ValueError(
            f"Transcript only reached {seen_turns} turns; cannot slice checkpoint {checkpoint_turn}"
        )

    return Transcript(
        metadata=transcript.metadata.model_copy(deep=True),
        events=retained_events,
        stop_reason=None,
    )


def build_checkpoint_metrics(
    rows: list[dict[str, Any]],
    *,
    dimension: str,
) -> dict[str, Any]:
    by_turn: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        checkpoint_turn = row.get("checkpoint_turn")
        if isinstance(checkpoint_turn, int):
            by_turn.setdefault(checkpoint_turn, []).append(row)

    checkpoints: list[dict[str, Any]] = []
    for checkpoint_turn in sorted(by_turn):
        items = by_turn[checkpoint_turn]
        scored_items = [item for item in items if infer_judge_status(item) == "ok"]
        raw_values: list[bool] = []
        for item in scored_items:
            value = get_verdict_dimension(item.get("verdict"), dimension)
            if is_valid_event_flag(value):
                raw_values.append(bool(value))
        flagged_count = sum(1 for value in raw_values if value)
        clear_count = len(raw_values) - flagged_count
        checkpoints.append(
            {
                "checkpoint_turn": checkpoint_turn,
                "count": len(items),
                "scored_count": len(scored_items),
                "judge_failures": len(items) - len(scored_items),
                "flagged_count": flagged_count,
                "clear_count": clear_count,
                "rate": (flagged_count / len(raw_values)) if raw_values else 0.0,
            }
        )

    return {
        "dimension": dimension,
        "checkpoints": checkpoints,
    }


def write_checkpoint_plot(metrics_payload: dict[str, Any], out_path: Path) -> None:
    checkpoints = metrics_payload.get("checkpoints")
    if not isinstance(checkpoints, list) or not checkpoints:
        raise ValueError("No checkpoint metrics available to plot")

    dimension = str(metrics_payload.get("dimension") or "policy_violation")
    turns = [int(item["checkpoint_turn"]) for item in checkpoints]
    rates = [float(item["rate"]) * 100.0 for item in checkpoints]
    counts = [int(item["scored_count"]) for item in checkpoints]
    width = 720
    height = 420
    left = 80
    right = 24
    top = 32
    bottom = 64
    plot_width = width - left - right
    plot_height = height - top - bottom

    min_turn = min(turns)
    max_turn = max(turns)
    turn_span = max(max_turn - min_turn, 1)

    def x_pos(turn: int) -> float:
        if len(turns) == 1:
            return left + (plot_width / 2.0)
        return left + ((turn - min_turn) / turn_span) * plot_width

    def y_pos(rate: float) -> float:
        clamped = min(max(rate, 0.0), 100.0)
        return top + ((100.0 - clamped) / 100.0) * plot_height

    point_pairs = [(x_pos(turn), y_pos(rate)) for turn, rate in zip(turns, rates)]
    polyline_points = " ".join(f"{x:.2f},{y:.2f}" for x, y in point_pairs)
    grid_values = [0, 25, 50, 75, 100]

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white" />',
    ]

    for grid_value in grid_values:
        y = y_pos(float(grid_value))
        lines.append(
            f'<line x1="{left}" y1="{y:.2f}" x2="{width - right}" y2="{y:.2f}" stroke="#d1d5db" stroke-width="1" />'
        )
        lines.append(
            f'<text x="{left - 10}" y="{y + 4:.2f}" font-size="12" text-anchor="end" fill="#374151">{grid_value}</text>'
        )

    lines.extend(
        [
            f'<line x1="{left}" y1="{top}" x2="{left}" y2="{height - bottom}" stroke="#111827" stroke-width="1.5" />',
            f'<line x1="{left}" y1="{height - bottom}" x2="{width - right}" y2="{height - bottom}" stroke="#111827" stroke-width="1.5" />',
            f'<polyline fill="none" stroke="#2563eb" stroke-width="2.5" points="{polyline_points}" />',
        ]
    )

    for (x, y), turn, rate, count in zip(point_pairs, turns, rates, counts):
        lines.append(
            f'<circle cx="{x:.2f}" cy="{y:.2f}" r="4" fill="#2563eb" />'
        )
        lines.append(
            f'<text x="{x:.2f}" y="{height - bottom + 22}" font-size="12" text-anchor="middle" fill="#111827">{turn}</text>'
        )
        lines.append(
            f'<text x="{x:.2f}" y="{y - 10:.2f}" font-size="12" text-anchor="middle" fill="#111827">n={count}</text>'
        )
        lines.append(
            f'<text x="{x:.2f}" y="{y - 24:.2f}" font-size="12" text-anchor="middle" fill="#2563eb">{rate:.1f}%</text>'
        )

    lines.extend(
        [
            f'<text x="{width / 2:.2f}" y="{height - 18}" font-size="14" text-anchor="middle" fill="#111827">Checkpoint turn</text>',
            (
                f'<text x="20" y="{height / 2:.2f}" font-size="14" text-anchor="middle" '
                f'transform="rotate(-90 20 {height / 2:.2f})" fill="#111827">'
                f'{html.escape(dimension)} flagged (%)</text>'
            ),
            (
                f'<text x="{width / 2:.2f}" y="18" font-size="16" text-anchor="middle" fill="#111827">'
                f'{html.escape(dimension)} by checkpoint turn</text>'
            ),
            "</svg>",
        ]
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")


def _backup_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.bak")


def _stash_existing_artifacts(paths: list[Path]) -> dict[Path, Path]:
    backups: dict[Path, Path] = {}
    for path in paths:
        if not path.exists():
            continue
        backup = _backup_path(path)
        if backup.exists():
            backup.unlink()
        path.replace(backup)
        backups[path] = backup
    return backups


def _restore_backup(path: Path, backup: Path) -> None:
    if path.exists():
        path.unlink()
    if backup.exists():
        backup.replace(path)


def _delete_backup(backup: Path) -> None:
    if backup.exists():
        backup.unlink()


async def _stream_results(
    tasks: list[CheckpointTask],
    *,
    limit: int,
    worker: Any,
    on_completion: Any,
) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(max(1, min(limit, len(tasks))))

    async def guarded(task: CheckpointTask) -> dict[str, Any]:
        async with semaphore:
            return await worker(task)

    running = [asyncio.create_task(guarded(task)) for task in tasks]
    results: list[dict[str, Any]] = []
    for completed in asyncio.as_completed(running):
        result = await completed
        results.append(result)
        await on_completion(result)
    return results


async def run_checkpoint_judge(
    *,
    run_dir: str | Path,
    checkpoint_step: int = 5,
    dimension: str = "policy_violation",
    out_dir: str | Path | None = None,
    judge_model_override: str | None = None,
    judge_dimensions_override: list[dict[str, Any] | str] | None = None,
    judge_n_override: int | None = None,
    concurrency_override: int | None = None,
) -> dict[str, Any]:
    if checkpoint_step <= 0:
        raise ValueError("checkpoint_step must be > 0")

    resolved_run_dir = resolve_path(run_dir)
    transcripts_path = resolved_run_dir / "transcripts.jsonl"
    config_path = resolved_run_dir / "config.yaml"
    taxonomy_path = resolved_run_dir.parent / "taxonomy.json"
    resolved_out_dir = resolve_path(out_dir) if out_dir is not None else (resolved_run_dir / "checkpoint_judge")

    if not transcripts_path.exists():
        raise FileNotFoundError(f"Transcript file not found: {transcripts_path}")
    if not config_path.exists():
        raise FileNotFoundError(f"Run config not found: {config_path}")
    if not taxonomy_path.exists():
        raise FileNotFoundError(f"Taxonomy file not found: {taxonomy_path}")

    config = load_checkpoint_judge_config(
        config_path,
        judge_model_override=judge_model_override,
        judge_dimensions_override=judge_dimensions_override,
        judge_n_override=judge_n_override,
        concurrency_override=concurrency_override,
    )

    transcript_rows = load_jsonl(transcripts_path)
    if not transcript_rows:
        raise ValueError(f"No transcripts found in {transcripts_path}")

    policy_raw = json.loads(taxonomy_path.read_text(encoding="utf-8"))
    permissible_by_name = permissible_by_behavior(policy_raw)
    judge_contract = build_judge_contract(
        template=JUDGE_SYSTEM_PROMPT,
        policy_raw=policy_raw,
        judge_dimensions=config.judge_dimensions,
        schema_name="transcript_judgment",
    )
    if dimension not in judge_contract["score_keys"]:
        raise ValueError(
            f"Unknown plot dimension '{dimension}'. Available: {sorted(judge_contract['score_keys'])}"
        )

    tasks: list[CheckpointTask] = []
    output_index = 0
    for row in transcript_rows:
        transcript = transcript_from_row(row)
        final_turns = count_inference_turns(transcript)
        for checkpoint_turn in checkpoint_turns(final_turns, checkpoint_step):
            tasks.append(
                CheckpointTask(
                    output_index=output_index,
                    transcript_row=row,
                    checkpoint_turn=checkpoint_turn,
                    final_transcript_turns=final_turns,
                    transcript=transcript,
                )
            )
            output_index += 1

    if not tasks:
        raise ValueError(
            f"No checkpoint prefixes available at step {checkpoint_step} in {transcripts_path}"
        )

    resolved_out_dir.mkdir(parents=True, exist_ok=True)
    scores_path = resolved_out_dir / "checkpoint_scores.jsonl"
    metrics_path = resolved_out_dir / "checkpoint_metrics.json"
    plot_path = resolved_out_dir / f"{dimension}_by_turn.svg"
    backups = _stash_existing_artifacts([scores_path, metrics_path, plot_path])

    async def worker(task: CheckpointTask) -> dict[str, Any]:
        try:
            checkpoint_transcript = slice_transcript_at_turn(task.transcript, task.checkpoint_turn)
            transcript_xml, index_to_message_id = checkpoint_transcript.format_transcript_xml(
                "target",
                skip_system=False,
            )
            judge_result = await run_transcript_judge(
                judge_model=config.judge_model,
                system_prompt=judge_contract["system_prompt"],
                user_message=f"# Transcript\n{transcript_xml}",
                transcript=checkpoint_transcript,
                index_to_message_id=index_to_message_id,
                score_keys=judge_contract["score_keys"],
                policy_raw=policy_raw,
                judge_n=config.judge_n,
                judge_temperature=config.judge_temperature,
                judge_max_tokens=config.judge_max_tokens,
                response_schema=judge_contract["response_schema"],
            )
            behavior = str(
                row_behavior(task.transcript_row)
                or ""
            )
            row = {
                "type": task.transcript_row.get("kind", ""),
                "test_case_id": task.transcript_row.get("test_case_id", ""),
                "behavior": task.transcript_row.get("behavior", ""),
                "permissible": policy_permissible(
                    permissible_by_name,
                    behavior,
                ),
                "target": task.transcript_row.get("target", ""),
                "tester_model": task.transcript_row.get("tester_model", ""),
                "checkpoint_turn": task.checkpoint_turn,
                "final_transcript_turns": task.final_transcript_turns,
                "judge_model": config.judge_model,
                "judge_status": judge_result["judge_status"],
                "judge_error": judge_result["judge_error"],
                "verdict": judge_result["verdict"],
                "dimensions": {"behavior": behavior},
            }
            if judge_result.get("multi_judge") is not None:
                row["multi_judge"] = judge_result["multi_judge"]
            return {
                "output_index": task.output_index,
                "score_row": row,
            }
        except Exception as exc:
            return {
                "output_index": task.output_index,
                "error": exc,
            }

    async def on_completion(result: dict[str, Any]) -> None:
        score_row = result.get("score_row")
        if isinstance(score_row, dict):
            append_jsonl_row(scores_path, score_row)

    results = sorted(
        await _stream_results(
            tasks,
            limit=config.concurrency,
            worker=worker,
            on_completion=on_completion,
        ),
        key=lambda item: item["output_index"],
    )
    successful_results = [item for item in results if item.get("error") is None]
    score_rows = [item["score_row"] for item in successful_results]

    for item in results:
        error = item.get("error")
        if error is not None:
            if not score_rows:
                for path, backup in backups.items():
                    _restore_backup(path, backup)
            else:
                for path in (metrics_path, plot_path):
                    backup = backups.get(path)
                    if backup is not None:
                        _restore_backup(path, backup)
                score_backup = backups.get(scores_path)
                if score_backup is not None:
                    _delete_backup(score_backup)
            raise error

    if score_rows:
        write_jsonl(scores_path, score_rows)
        metrics_payload = build_checkpoint_metrics(score_rows, dimension=dimension)
        write_json(metrics_path, metrics_payload)
        write_checkpoint_plot(metrics_payload, plot_path)

    for backup in backups.values():
        _delete_backup(backup)

    return {
        "scores_path": str(scores_path),
        "metrics_path": str(metrics_path),
        "plot_path": str(plot_path),
        "count": len(score_rows),
    }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    result = asyncio.run(
        run_checkpoint_judge(
            run_dir=args.run_dir,
            checkpoint_step=args.checkpoint_step,
            dimension=args.dimension,
            out_dir=args.out_dir,
            judge_model_override=args.judge_model,
            judge_dimensions_override=args.judge_dimensions,
            judge_n_override=args.judge_n,
            concurrency_override=args.concurrency,
        )
    )
    print(f"Wrote {result['count']} checkpoint score rows")
    print(result["scores_path"])
    print(result["metrics_path"])
    print(result["plot_path"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
