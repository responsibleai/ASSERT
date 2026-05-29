# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Judge stability experiment: score full conversations with different models, temperatures, and n values."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

from assert_eval.core.io import (
    load_jsonl,
    permissible_by_behavior,
    policy_permissible,
    write_jsonl,
    row_behavior,
    row_factors,
)
from assert_eval.core.judge import build_judge_contract, run_transcript_judge
from assert_eval.core.transcript import Transcript, TranscriptEvent, TranscriptMetadata
from assert_eval.stages.judge import JUDGE_SYSTEM_PROMPT


def transcript_from_row(row: dict) -> Transcript:
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


@dataclass(frozen=True)
class JudgeRunConfig:
    name: str
    judge_model: str
    temperature: float
    judge_n: int
    max_tokens: int = 12000


RESULTS_BASE = Path("artifacts/results/relationship-entanglement-v1")

RUN_DIRS = [
    "gpt54-tester_gpt54nano-target_gpt54-judge_30turns",
    "gpt54mini-tester_gpt54nano-target_gpt54-judge_30turns",
    "gpt54nano-tester_gpt54nano-target_gpt54-judge_30turns",
    "gpt5rrf0726-tester_gpt54nano-target_gpt54-judge_30turns",
]

JUDGE_DIMENSIONS = ["harm_actionability", "overrefusal"]

# Q1: intra-judge stability (GPT-5.4)
Q1_CONFIGS = [
    JudgeRunConfig(name="gpt54_temp0_run1", judge_model="azure/gpt-5.4", temperature=0.0, judge_n=1),
    JudgeRunConfig(name="gpt54_temp0_run2", judge_model="azure/gpt-5.4", temperature=0.0, judge_n=1),
    JudgeRunConfig(name="gpt54_temp0_run3", judge_model="azure/gpt-5.4", temperature=0.0, judge_n=1),
    JudgeRunConfig(name="gpt54_temp0_run4", judge_model="azure/gpt-5.4", temperature=0.0, judge_n=1),
    JudgeRunConfig(name="gpt54_temp0_run5", judge_model="azure/gpt-5.4", temperature=0.0, judge_n=1),
    JudgeRunConfig(name="gpt54_temp03_run1", judge_model="azure/gpt-5.4", temperature=0.3, judge_n=1),
    JudgeRunConfig(name="gpt54_temp03_run2", judge_model="azure/gpt-5.4", temperature=0.3, judge_n=1),
    JudgeRunConfig(name="gpt54_temp03_run3", judge_model="azure/gpt-5.4", temperature=0.3, judge_n=1),
]

# Q2: inter-judge agreement
Q2_CONFIGS = [
    JudgeRunConfig(name="gpt54mini_run1", judge_model="azure/gpt-5.4-mini", temperature=0.0, judge_n=1),
    JudgeRunConfig(name="deepseek_run1", judge_model="azure/DeepSeek-V3.2", temperature=0.0, judge_n=1),
]

# Q6: multi-judge ensembling
Q6_CONFIGS = [
    JudgeRunConfig(name="gpt54_n3_run1", judge_model="azure/gpt-5.4", temperature=0.0, judge_n=3),
    JudgeRunConfig(name="gpt54_n3_run2", judge_model="azure/gpt-5.4", temperature=0.0, judge_n=3),
    JudgeRunConfig(name="gpt54_n5_run1", judge_model="azure/gpt-5.4", temperature=0.0, judge_n=5),
    JudgeRunConfig(name="gpt54_n5_run2", judge_model="azure/gpt-5.4", temperature=0.0, judge_n=5),
]

ALL_CONFIGS = Q1_CONFIGS + Q2_CONFIGS + Q6_CONFIGS


async def score_transcripts(
    inference_rows: list[dict],
    policy_raw: dict,
    judge_contract: dict,
    config: JudgeRunConfig,
    concurrency: int,
) -> list[dict]:
    semaphore = asyncio.Semaphore(concurrency)
    results: list[dict] = []
    permissible_by_name = permissible_by_behavior(policy_raw)

    async def score_one(idx: int, row: dict) -> dict:
        async with semaphore:
            transcript = transcript_from_row(row)
            transcript_xml, index_to_message_id = transcript.format_transcript_xml(
                "target", skip_system=False,
            )
            judge_result = await run_transcript_judge(
                judge_model=config.judge_model,
                system_prompt=judge_contract["system_prompt"],
                user_message=f"# Transcript\n{transcript_xml}",
                transcript=transcript,
                index_to_message_id=index_to_message_id,
                score_keys=judge_contract["score_keys"],
                policy_raw=policy_raw,
                judge_n=config.judge_n,
                judge_temperature=config.temperature,
                judge_max_tokens=config.max_tokens,
                response_schema=judge_contract["response_schema"],
            )
            score_row = {
                "test_case_id": row.get("test_case_id", ""),
                "behavior": row.get("behavior", ""),
                "target": row.get("target", ""),
                "tester_model": row.get("tester_model", ""),
                "judge_model": config.judge_model,
                "judge_temperature": config.temperature,
                "judge_n": config.judge_n,
                "judge_status": judge_result["judge_status"],
                "judge_error": judge_result["judge_error"],
                "verdict": judge_result["verdict"],
            }
            behavior = row_behavior(row)
            score_row["dimensions"] = {"behavior": behavior}
            score_row["permissible"] = policy_permissible(permissible_by_name, behavior)
            if judge_result.get("multi_judge") is not None:
                score_row["multi_judge"] = judge_result["multi_judge"]
            return score_row

    tasks = [asyncio.create_task(score_one(i, row)) for i, row in enumerate(inference_rows)]
    for coro in asyncio.as_completed(tasks):
        result = await coro
        results.append(result)
        status = result.get("judge_status", "?")
        sid = result.get("test_case_id", "?")
        print(f"  {sid}: {status}")

    # Sort by test_case_id for deterministic output
    results.sort(key=lambda r: r.get("test_case_id", ""))
    return results


async def run_experiment(
    run_dir_name: str,
    config: JudgeRunConfig,
    concurrency: int,
) -> None:
    run_dir = RESULTS_BASE / run_dir_name
    out_dir = run_dir / "judge_stability" / config.name
    scores_path = out_dir / "scores.jsonl"

    if scores_path.exists():
        existing = load_jsonl(scores_path)
        print(f"SKIP {run_dir_name}/{config.name} — already has {len(existing)} scores")
        return

    print(f"\nRUN {run_dir_name}/{config.name}")
    print(f"  model={config.judge_model} temp={config.temperature} n={config.judge_n}")

    inference_rows = load_jsonl(run_dir / "inference_set.jsonl")
    policy_raw = json.loads((run_dir.parent / "taxonomy.json").read_text())
    judge_contract = build_judge_contract(
        template=JUDGE_SYSTEM_PROMPT,
        policy_raw=policy_raw,
        judge_dimensions=JUDGE_DIMENSIONS,
        schema_name="transcript_judgment",
    )

    results = await score_transcripts(inference_rows, policy_raw, judge_contract, config, concurrency)

    out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(scores_path, results)
    ok = sum(1 for r in results if r.get("judge_status") == "ok")
    print(f"  Done: {ok}/{len(results)} ok -> {scores_path}")


async def main(configs: list[JudgeRunConfig], concurrency: int) -> None:
    for config in configs:
        for run_dir_name in RUN_DIRS:
            await run_experiment(run_dir_name, config, concurrency)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Judge stability experiment runner")
    parser.add_argument(
        "--phase",
        choices=["q1", "q2", "q6", "all"],
        default="all",
        help="Which experiment phase to run.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=10,
        help="Max concurrent judge calls.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.phase == "q1":
        configs = Q1_CONFIGS
    elif args.phase == "q2":
        configs = Q2_CONFIGS
    elif args.phase == "q6":
        configs = Q6_CONFIGS
    else:
        configs = ALL_CONFIGS

    total_calls = len(configs) * len(RUN_DIRS) * 50
    print(f"Judge stability experiment: {len(configs)} configs × {len(RUN_DIRS)} run dirs × 50 test_set")
    print(f"Total judge calls: ~{total_calls}")
    print(f"Concurrency: {args.concurrency}")
    print()

    asyncio.run(main(configs, args.concurrency))
