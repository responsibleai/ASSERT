"""Score unified inference-set artifacts."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import traceback
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

from assert_eval.config import resolve_stage_paths
from assert_eval.core.io import SCORES_FILE, INFERENCE_SET_FILE, row_factors
from assert_eval.core.io import append_jsonl_row, load_jsonl, load_prompt_text, resolve_path
from assert_eval.core.judge import (
    build_judge_contract,
    infer_judge_status,
    run_transcript_judge as run_llm_judge,
)
from assert_eval.core.model_client import LLMAuthError, LLMContentFilterError, LLMInputError, LLMRateLimitError, LLMProviderError
from assert_eval.core.transcript import Transcript, TranscriptEvent, TranscriptMetadata
from assert_eval.viewer_read_model import build_run_viewer_artifacts

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
    inference_set_path: Path,
) -> str:
    """Deterministic hash of inputs that affect judge output."""
    inference_set_sha = hashlib.sha256(inference_set_path.read_bytes()).hexdigest()
    key = json.dumps(
        {
            "judge_model": judge_model,
            "judge_temperature": judge_temperature,
            "judge_max_tokens": judge_max_tokens,
            "judge_reasoning_effort": judge_reasoning_effort,
            "judge_n": judge_n,
            "judge_dimensions": judge_dimensions,
            "taxonomy": policy_raw,
            "system_prompt_sha": hashlib.sha256(system_prompt.encode("utf-8")).hexdigest(),
            "inference_set_sha": inference_set_sha,
        },
        sort_keys=True,
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


async def run_judge(
    *,
    inference_set_path: str,
    taxonomy_path: str | None = None,
    save_dir: str | None = None,
    evaluation: Any,
    judge_dimensions: list[dict[str, Any]] | None = None,
    forced: bool = False,
    heartbeat: Any = None,
) -> dict[str, Any]:
    """Score inference rows and write score artifacts."""
    judge_model = str(evaluation.judge.model.name)
    judge_temperature = evaluation.judge.model.temperature
    judge_max_tokens = evaluation.judge.model.max_tokens
    judge_reasoning_effort = evaluation.judge.model.reasoning_effort
    judge_n = evaluation.judge.n
    resolved_inference_set_path = resolve_path(inference_set_path)
    rows = load_jsonl(resolved_inference_set_path)
    if not rows:
        raise ValueError(f"No inference rows found in {inference_set_path}")

    out_dir = resolve_path(save_dir or str(resolved_inference_set_path.parent))
    out_dir.mkdir(parents=True, exist_ok=True)
    if not taxonomy_path:
        raise ValueError("judge stage requires taxonomy_path")
    resolved_taxonomy_path = resolve_path(taxonomy_path)
    if not resolved_taxonomy_path.exists():
        raise ValueError(f"Taxonomy file not found: {taxonomy_path}")
    try:
        policy_raw = json.loads(resolved_taxonomy_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid JSON in taxonomy file {resolved_taxonomy_path}: {exc}"
        ) from exc
    judge_contract = build_judge_contract(
        template=JUDGE_SYSTEM_PROMPT,
        policy_raw=policy_raw,
        judge_dimensions=judge_dimensions or [],
        schema_name="transcript_judgment",
    )

    async def score_row(row: dict[str, Any]) -> dict[str, Any]:
        """Score a single inference row with the judge model."""
        transcript_metadata = TranscriptMetadata(
            kind=str(row.get("type") or ""),
            test_case_id=str(row.get("test_case_id") or ""),
            behavior=str(row.get("behavior") or ""),
            target=str(row.get("target") or ""),
            tester_model=str(row.get("tester_model") or ""),
            dimensions=row_factors(row),
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
            "type": row.get("type", ""),
            "test_case_id": row.get("test_case_id", ""),
            "behavior": row.get("behavior", ""),
            "judge_model": judge_model,
            "target": row.get("target", ""),
            "tester_model": row.get("tester_model", ""),
            "judge_status": infer_judge_status({
                "judge_status": judge_result["judge_status"],
                "verdict": judge_result["verdict"],
            }),
            "judge_error": judge_result["judge_error"],
            "verdict": judge_result["verdict"],
        }
        dimensions = row_factors(row)
        if dimensions:
            score_row["dimensions"] = dimensions
        if judge_result.get("multi_judge") is not None:
            score_row["multi_judge"] = judge_result["multi_judge"]
        return score_row

    async def worker(item: tuple[int, dict[str, Any]]) -> dict[str, Any]:
        """Wrap row scoring so concurrent execution returns structured errors.

        Auth errors fail the stage immediately — they're never transient
        and continuing only burns tokens. Rate-limit, provider, and
        input errors after the per-call retry budget is exhausted are
        treated as per-row failures: the test case is recorded with an
        ``error`` field and the stage continues. Without this, a single
        unrecoverable judge call would discard all the work the other
        concurrent calls have already finished.
        """
        output_index, row = item
        try:
            return {
                "output_index": output_index,
                "score_row": await score_row(row),
            }
        except LLMContentFilterError as exc:
            # Adversarial-eval workloads routinely send transcripts the
            # judge's content filter will reject (XPIA payloads, PII
            # leakage examples, security-attack scenarios). Treat these
            # as soft per-row failures so the tolerance logic below can
            # decide whether the run is still publishable. Caught
            # explicitly (LLMContentFilterError subclasses LLMInputError)
            # so adversarial seeds emit a quieter debug log instead of
            # the warning + filter_skipped score the generic input-error
            # handler below would emit for every one of them.
            seed_id = row.get("seed_id", "?")
            log.debug(
                "Judge worker hit provider content filter for seed %s: %s",
                seed_id, exc,
            )
            return {
                "output_index": output_index,
                "error": exc,
            }
        except LLMAuthError:
            raise
        except LLMInputError as exc:
            # Judge-side input refusal (e.g. Azure content filter rejecting
            # a transcript whose adversarial content the judge LLM can't
            # process). This is per-test-case data, not a global pipeline
            # problem: a different transcript will judge cleanly. Record a
            # filter_skipped score row so the test case isn't lost and the
            # stage can move on. Mirrors the target_input_refused and
            # tester_input_refused handling in inference. (Absorbed from
            # PR #44 commit dcaa91f — was previously only available as a
            # benchmark monkey-patch in scripts/benchmark.py.)
            test_case_id = row.get("test_case_id", "?")
            log.warning(
                "Judge content-filter refusal for test case %s: %s",
                test_case_id, exc,
            )
            dimensions = row_factors(row)
            score_row_filter_skipped: dict[str, Any] = {
                "type": row.get("type", ""),
                "test_case_id": test_case_id,
                "behavior": row.get("behavior", ""),
                "judge_model": judge_model,
                "target": row.get("target", ""),
                "tester_model": row.get("tester_model", ""),
                "judge_status": "filter_skipped",
                "judge_error": f"judge_input_refused: {exc}",
                "verdict": {},
            }
            if dimensions:
                score_row_filter_skipped["dimensions"] = dimensions
            return {
                "output_index": output_index,
                "score_row": score_row_filter_skipped,
            }
        except (LLMRateLimitError, LLMProviderError) as exc:
            test_case_id = row.get("test_case_id", "?")
            log.warning(
                "Judge call exhausted retries for test case %s (%s): %s",
                test_case_id, type(exc).__name__, exc,
            )
            return {
                "output_index": output_index,
                "error": exc,
            }
        except (json.JSONDecodeError, ValueError) as exc:
            test_case_id = row.get("test_case_id", "?")
            log.debug(
                "Judge worker parse/validation error for test case %s: %s\n%s",
                test_case_id, exc, traceback.format_exc(),
            )
            return {
                "output_index": output_index,
                "error": exc,
            }
        except Exception as exc:
            test_case_id = row.get("test_case_id", "?")
            log.debug(
                "Judge worker failed for test case %s: %s\n%s",
                test_case_id, exc, traceback.format_exc(),
            )
            return {
                "output_index": output_index,
                "error": exc,
            }

    scores_path = out_dir / SCORES_FILE

    # Resume: load already-scored (kind, test_case_id) pairs and skip them, but only
    # if the judge configuration and inference-set file hasn't changed since the
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
        inference_set_path=resolved_inference_set_path,
    )
    config_hash_path = out_dir / _JUDGE_CONFIG_HASH_FILE
    if scores_path.exists():
        if forced:
            # User explicitly forced this stage (directly or via the runner's
            # --force-stage cascade). Discard the cached output unconditionally
            # rather than relying on a hash mismatch; regenerated upstream
            # inference rows may produce byte-identical scores under stable
            # judge config, which would otherwise leave the cache intact.
            scores_path.unlink()
        else:
            stored_hash = config_hash_path.read_text(encoding="utf-8").strip() if config_hash_path.exists() else None
            if stored_hash is not None and stored_hash != config_hash:
                log.warning(
                    f"Judge config or inference set changed since last run - discarding {scores_path} and starting fresh"
                )
                scores_path.unlink()
            else:
                for prior in load_jsonl(scores_path):
                    sid = prior.get("test_case_id")
                    if sid:
                        completed_keys.add((str(prior.get("type") or ""), str(sid)))
    if completed_keys:
        log.info(
            f"Resuming judge: {len(completed_keys)} inference rows already scored, skipping"
        )
    config_hash_path.write_text(config_hash, encoding="utf-8")

    pending = [
        (i, row) for i, row in enumerate(rows)
        if (str(row.get("type") or ""), str(row.get("test_case_id", ""))) not in completed_keys
    ]

    semaphore = asyncio.Semaphore(max(1, min(evaluation.inference.concurrency, len(pending) or 1)))

    async def guard(item: tuple[int, dict[str, Any]]) -> dict[str, Any]:
        async with semaphore:
            return await worker(item)

    tasks = [asyncio.create_task(guard(item)) for item in pending]
    # Optional heartbeat hook: the runner injects one so manifest.json can
    # report live progress every 30s during scoring. Stages must tolerate
    # heartbeat being absent (e.g. unit tests, ad-hoc scripts), hence the
    # defensive None checks.
    total = len(tasks)
    cached_count = len(completed_keys)
    if heartbeat is not None:
        try:
            heartbeat.set_progress(
                stage="judge",
                completed=cached_count,
                total=cached_count + total,
            )
        except Exception:  # noqa: BLE001
            heartbeat = None
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
        if heartbeat is not None:
            try:
                heartbeat.set_progress(
                    stage="judge",
                    completed=cached_count + written_rows + len(errors),
                    total=cached_count + total,
                    errors=len(errors),
                )
            except Exception:  # noqa: BLE001
                heartbeat = None

    # Always rebuild viewer artifacts so the on-disk read model reflects the
    # current scores.jsonl, even when a row failed and we are about to raise.
    build_run_viewer_artifacts(out_dir)
    # Per-row failures should not kill the stage as long as *some* rows
    # succeeded. The errors are surfaced via judge_failures in the
    # returned summary and as judge_status != "ok" in scores.jsonl, so
    # downstream consumers (metrics, viewer, CSV) can present them
    # without losing the rows that did succeed. The stage hard-fails
    # outright when (a) no rows succeeded AND no prior scores were
    # cached (failure is systemic — auth, config), or (b) the per-row
    # failure rate exceeds 10% on runs of >=20 transcripts (mirrors
    # rollout.py's ASSERT_ROLLOUT_ERROR_FAIL_RATIO ceiling, so a near-
    # broken run doesn't silently publish thin scores). Below the
    # tolerance, log and continue — adversarial-eval workloads will
    # routinely trip the judge model's provider-side content filter
    # (e.g. Azure's "potentially high-risk cyber activity" check on
    # XPIA/PII transcripts) and a small residual count is expected.
    if errors and written_rows == 0 and not completed_keys:
        log.error(
            "Judge stage failed: all %d row(s) errored and no prior scores were cached",
            len(errors),
        )
        raise errors[0]
    if errors:
        total_pending = len(pending)
        tolerance = total_pending // 10 if total_pending >= 20 else 0
        if 0 < len(errors) <= tolerance:
            log.warning(
                "[judge] tolerating %d/%d row-level failures (<= %d): "
                "first error was %s. The remaining %d scores will proceed "
                "to metrics.",
                len(errors), total_pending, tolerance, type(errors[0]).__name__,
                written_rows,
            )
        else:
            log.error(
                "Judge stage failed: %d/%d new rows errored, exceeding the "
                "10%% tolerance (first error was %s)",
                len(errors), total_pending, type(errors[0]).__name__,
            )
            raise errors[0]

    judge_failures = sum(
        1 for row in load_jsonl(scores_path) if infer_judge_status(row) != "ok"
    )
    return {
        "scores_path": str(scores_path),
        "count": len(completed_keys) + written_rows,
        "new_count": written_rows,
        "cached_count": len(completed_keys),
        "judge_failures": judge_failures,
        # Errored rows are NOT written to scores.jsonl so that re-running
        # the stage will pick them up via the existing resume logic and
        # re-attempt them. We surface the count here for the runner /
        # benchmark CSV / metrics so the user can see how many test_set
        # the next run will need to retry.
        "errored_count": len(errors),
    }


async def run(ctx: dict[str, Any], raw_cfg: dict[str, Any]) -> dict[str, str]:
    """Validate config and run the scoring workflow."""
    evaluation = ctx.get("evaluation")
    if evaluation is None or not evaluation.judge.model:
        raise ValueError("judge stage requires evaluation.judge.model")
    judge_dimensions = evaluation.judge.dimensions if evaluation.judge is not None else []
    cfg = resolve_stage_paths(
        {
            "inference_set_path": raw_cfg.get("inference_set_path") or str(Path(ctx["run_root"]) / INFERENCE_SET_FILE),
            "taxonomy_path": raw_cfg.get("taxonomy_path") or ctx.get("taxonomy_path") or str(Path(ctx["suite_root"]) / "taxonomy.json"),
            "save_dir": raw_cfg.get("save_dir") or str(ctx["run_root"]),
        },
        cfg_path=ctx["config_path"],
        artifacts_root=ctx["artifacts_root"],
    )
    result = await run_judge(
        inference_set_path=cfg["inference_set_path"],
        taxonomy_path=cfg.get("taxonomy_path"),
        save_dir=cfg.get("save_dir"),
        evaluation=ctx["evaluation"],
        judge_dimensions=judge_dimensions,
        forced=bool(ctx.get("_stage_forced", False)),
        heartbeat=ctx.get("_heartbeat") if isinstance(ctx, dict) else None,
    )
    return {
        "scores_path": result["scores_path"],
        "_summary": {
            "count": result.get("count", 0),
            "new_count": result.get("new_count", 0),
            "cached_count": result.get("cached_count", 0),
            "failures": result.get("judge_failures", 0),
            # Surfaced so the runner can skip finalize_artifact_plan when
            # any per-row error occurred. A partial scores.jsonl must
            # not be tagged as a complete cacheable artifact -- a future
            # cache hit would silently reuse the smaller file.
            "errored_count": int(result.get("errored_count", 0) or 0),
        },
    }
