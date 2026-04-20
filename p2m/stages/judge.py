"""Score unified transcript rollout artifacts."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from p2m.config import resolve_stage_paths
from p2m.core.io import SCORES_FILE, TRANSCRIPTS_FILE, row_factors
from p2m.core.io import append_jsonl_row, load_jsonl, load_prompt_text, resolve_path
from p2m.core.judge import (
    build_judge_contract,
    infer_judge_status,
    run_transcript_judge as run_llm_judge,
)
from p2m.core.transcript import Transcript, TranscriptEvent, TranscriptMetadata
from p2m.viewer_read_model import build_run_viewer_artifacts

SCOPE = "run"
SUITE_OUTPUT = None

JUDGE_SYSTEM_PROMPT = load_prompt_text("judge_system.md")

_JUDGE_CONFIG_HASH_FILE = ".judge_config_hash"


def _judge_config_fingerprint(
    *,
    judge_model: str,
    judge_temperature: float | None,
    judge_max_tokens: int | None,
    judge_reasoning_effort: str | None,
    judge_n: int,
    judge_dimensions: list[dict[str, Any]],
    policy_raw: dict[str, Any],
    system_prompt: str,
    transcripts_path: Path,
) -> str:
    """Deterministic hash of inputs that affect judge output."""
    transcripts_sha = hashlib.sha256(transcripts_path.read_bytes()).hexdigest()
    key = json.dumps(
        {
            "judge_model": judge_model,
            "judge_temperature": judge_temperature,
            "judge_max_tokens": judge_max_tokens,
            "judge_reasoning_effort": judge_reasoning_effort,
            "judge_n": judge_n,
            "judge_dimensions": judge_dimensions,
            "policy": policy_raw,
            "system_prompt_sha": hashlib.sha256(system_prompt.encode("utf-8")).hexdigest(),
            "transcripts_sha": transcripts_sha,
        },
        sort_keys=True,
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


async def run_judge(
    *,
    transcripts_path: str,
    policy_path: str | None = None,
    save_dir: str | None = None,
    evaluation: Any,
    judge_dimensions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Score transcript rows and write score artifacts."""
    judge_model = str(evaluation.judge.model.name)
    judge_temperature = evaluation.judge.model.temperature
    judge_max_tokens = evaluation.judge.model.max_tokens
    judge_reasoning_effort = evaluation.judge.model.reasoning_effort
    judge_n = evaluation.judge.n
    resolved_transcripts_path = resolve_path(transcripts_path)
    rows = load_jsonl(resolved_transcripts_path)
    if not rows:
        raise ValueError(f"No transcripts found in {transcripts_path}")

    out_dir = resolve_path(save_dir or str(resolved_transcripts_path.parent))
    out_dir.mkdir(parents=True, exist_ok=True)
    if not policy_path:
        raise ValueError("judge stage requires policy_path")
    resolved_policy_path = resolve_path(policy_path)
    if not resolved_policy_path.exists():
        raise ValueError(f"Policy file not found: {policy_path}")
    policy_raw = json.loads(resolved_policy_path.read_text(encoding="utf-8"))
    judge_contract = build_judge_contract(
        template=JUDGE_SYSTEM_PROMPT,
        policy_raw=policy_raw,
        judge_dimensions=judge_dimensions or [],
        schema_name="transcript_judgment",
    )

    async def score_row(row: dict[str, Any]) -> dict[str, Any]:
        """Score a single transcript row with the judge model."""
        transcript_metadata = TranscriptMetadata(
            kind=str(row.get("kind") or ""),
            seed_id=str(row.get("seed_id") or ""),
            concept=str(row.get("concept") or ""),
            target=str(row.get("target") or ""),
            auditor_model=str(row.get("auditor_model") or ""),
            factors=row_factors(row),
        )
        transcript = Transcript(
            metadata=transcript_metadata,
            events=[
                TranscriptEvent.model_validate(event)
                for event in row.get("events", [])
                if isinstance(event, dict)
            ],
            stop_reason=row.get("stop_reason"),
        )
        transcript_xml, index_to_message_id = transcript.format_transcript_xml(
            "target",
            skip_system=False,
        )
        judge_result = await run_llm_judge(
            judge_model=judge_model,
            system_prompt=judge_contract["system_prompt"],
            user_message=f"# Transcript\n{transcript_xml}",
            transcript=transcript,
            index_to_message_id=index_to_message_id,
            score_keys=judge_contract["score_keys"],
            policy_raw=policy_raw,
            judge_n=judge_n,
            judge_temperature=judge_temperature,
            judge_max_tokens=judge_max_tokens,
            response_schema=judge_contract["response_schema"],
            reasoning_effort=judge_reasoning_effort,
        )

        score_row = {
            "kind": row.get("kind", ""),
            "seed_id": row.get("seed_id", ""),
            "concept": row.get("concept", ""),
            "judge_model": judge_model,
            "target": row.get("target", ""),
            "auditor_model": row.get("auditor_model", ""),
            "judge_status": infer_judge_status({
                "judge_status": judge_result["judge_status"],
                "verdict": judge_result["verdict"],
            }),
            "judge_error": judge_result["judge_error"],
            "verdict": judge_result["verdict"],
        }
        factors = row_factors(row)
        if factors:
            score_row["factors"] = factors
        if judge_result.get("multi_judge") is not None:
            score_row["multi_judge"] = judge_result["multi_judge"]
        return score_row

    async def worker(item: tuple[int, dict[str, Any]]) -> dict[str, Any]:
        """Wrap row scoring so concurrent execution returns structured errors."""
        output_index, row = item
        try:
            return {
                "output_index": output_index,
                "score_row": await score_row(row),
            }
        except Exception as exc:
            return {
                "output_index": output_index,
                "error": exc,
            }

    scores_path = out_dir / SCORES_FILE

    # Resume: load already-scored (kind, seed_id) pairs and skip them, but only
    # if the judge configuration and transcripts file haven't changed since the
    # last run.
    completed_keys: set[tuple[str, str]] = set()
    config_hash = _judge_config_fingerprint(
        judge_model=judge_model,
        judge_temperature=judge_temperature,
        judge_max_tokens=judge_max_tokens,
        judge_reasoning_effort=judge_reasoning_effort,
        judge_n=judge_n,
        judge_dimensions=judge_dimensions or [],
        policy_raw=policy_raw,
        system_prompt=judge_contract["system_prompt"],
        transcripts_path=resolved_transcripts_path,
    )
    config_hash_path = out_dir / _JUDGE_CONFIG_HASH_FILE
    if scores_path.exists():
        stored_hash = config_hash_path.read_text(encoding="utf-8").strip() if config_hash_path.exists() else None
        if stored_hash is not None and stored_hash != config_hash:
            logging.warning(
                "Judge config or transcripts changed since last run — discarding %s and starting fresh",
                scores_path,
            )
            scores_path.unlink()
        else:
            for prior in load_jsonl(scores_path):
                sid = prior.get("seed_id")
                if sid:
                    completed_keys.add((str(prior.get("kind") or ""), str(sid)))
    if completed_keys:
        logging.info(
            "Resuming judge: %d transcripts already scored, skipping",
            len(completed_keys),
        )
    config_hash_path.write_text(config_hash, encoding="utf-8")

    pending = [
        (i, row) for i, row in enumerate(rows)
        if (str(row.get("kind") or ""), str(row.get("seed_id", ""))) not in completed_keys
    ]

    semaphore = asyncio.Semaphore(max(1, min(evaluation.rollout.concurrency, len(pending) or 1)))

    async def guard(item: tuple[int, dict[str, Any]]) -> dict[str, Any]:
        async with semaphore:
            return await worker(item)

    tasks = [asyncio.create_task(guard(item)) for item in pending]
    errors: list[Exception] = []
    written_rows = 0
    for completed_task in asyncio.as_completed(tasks):
        result = await completed_task
        score = result.get("score_row")
        if score is not None:
            append_jsonl_row(scores_path, score)
            written_rows += 1
        error = result.get("error")
        if error is not None:
            errors.append(error)

    # Always rebuild viewer artifacts so the on-disk read model reflects the
    # current scores.jsonl, even when a row failed and we are about to raise.
    build_run_viewer_artifacts(out_dir)
    if errors:
        raise errors[0]

    judge_failures = sum(
        1 for row in load_jsonl(scores_path) if infer_judge_status(row) != "ok"
    )
    return {
        "scores_path": str(scores_path),
        "count": len(completed_keys) + written_rows,
        "judge_failures": judge_failures,
    }


async def run(ctx: dict[str, Any], raw_cfg: dict[str, Any]) -> dict[str, str]:
    """Validate config and run the scoring workflow."""
    evaluation = ctx.get("evaluation")
    if evaluation is None or not evaluation.judge.model:
        raise ValueError("judge stage requires evaluation.judge.model")
    judge_dimensions = evaluation.judge.dimensions if evaluation.judge is not None else []
    cfg = resolve_stage_paths(
        {
            "transcripts_path": raw_cfg.get("transcripts_path") or str(Path(ctx["run_root"]) / TRANSCRIPTS_FILE),
            "policy_path": raw_cfg.get("policy_path") or str(Path(ctx["suite_root"]) / "policy.json"),
            "save_dir": raw_cfg.get("save_dir") or str(ctx["run_root"]),
        },
        cfg_path=ctx["config_path"],
        artifacts_root=ctx["artifacts_root"],
    )
    result = await run_judge(
        transcripts_path=cfg["transcripts_path"],
        policy_path=cfg.get("policy_path"),
        save_dir=cfg.get("save_dir"),
        evaluation=ctx["evaluation"],
        judge_dimensions=judge_dimensions,
    )
    return {
        "scores_path": result["scores_path"],
    }
