# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Evaluate ASSERT inference transcripts as a Foundry Dataset target."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from assert_ai.config import load_config

from artifacts import ArtifactError, prepare_precomputed_rows
from evaluators import EvaluatorError, compile_evaluator_specs
from foundry import (
    FoundryAdapterError,
    create_clients,
    create_evaluation,
    criteria_hash,
    dataset_lineage_metadata,
    dataset_schema,
    ensure_dataset,
    ensure_evaluators,
    metadata,
    prepare_local_asset,
    run_dataset_evaluation,
    turn_criteria,
)


HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE.parent / "eval_config.yaml"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Join ASSERT test_set.jsonl and inference_set.jsonl and evaluate in Foundry."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--test-set", type=Path)
    parser.add_argument("--inference-set", type=Path)
    parser.add_argument("--output-dir", type=Path, default=HERE / "output")
    parser.add_argument(
        "--project-endpoint",
        default=(
            os.environ.get("AZURE_AI_PROJECT_ENDPOINT")
            or os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
        ),
    )
    parser.add_argument("--model-deployment", default=os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME"))
    parser.add_argument("--timeout-seconds", type=float, default=1800)
    parser.add_argument("--poll-seconds", type=float, default=5)
    parser.add_argument("--dry-run", "--prepare", action="store_true", dest="dry_run")
    return parser


def main() -> int:
    args = _parser().parse_args()
    config_path = args.config.resolve()
    raw = load_config(config_path)
    suite = str(raw.get("suite") or "").strip()
    run = str(raw.get("run") or "").strip()
    if not suite or not run:
        raise FoundryAdapterError("ASSERT config must define non-empty suite and run values")

    results_root = (config_path.parents[2] / "artifacts" / "results").resolve()
    test_set_path = (args.test_set or results_root / suite / "test_set.jsonl").resolve()
    inference_set_path = (
        args.inference_set or results_root / suite / run / "inference_set.jsonl"
    ).resolve()
    dataset = prepare_precomputed_rows(
        test_set_path,
        inference_set_path,
        suite=suite,
        run=run,
    )
    local_path, _ = prepare_local_asset(
        dataset,
        output_dir=args.output_dir,
        dataset_name=f"{suite}-assert-precomputed",
        lineage={
            "assert_suite": suite,
            "assert_run": run,
            "route": "precomputed-dataset",
            "scores_jsonl_used": False,
        },
    )
    specs = compile_evaluator_specs(config_path, level="turn")
    print(
        f"Prepared {len(dataset.rows)} joined rows from test_set.jsonl and "
        "inference_set.jsonl. scores.jsonl was not read."
    )
    if args.dry_run:
        print("Dry run complete: no Foundry resources were created or modified.")
        return 0
    if not args.project_endpoint or not args.model_deployment:
        raise FoundryAdapterError(
            "Cloud mode requires AZURE_AI_PROJECT_ENDPOINT (or "
            "FOUNDRY_PROJECT_ENDPOINT) and "
            "AZURE_AI_MODEL_DEPLOYMENT_NAME (or matching CLI options)"
        )

    credential, project_client, openai_client = create_clients(args.project_endpoint)
    try:
        dataset_ref = ensure_dataset(
            project_client,
            name=f"{suite}-assert-precomputed",
            dataset=dataset,
            local_path=local_path,
        )
        versions = ensure_evaluators(project_client, specs)
        run_metadata = metadata(
            suite=suite,
            run=run,
            dataset_hash=dataset.content_sha256,
            route="precomputed-dataset",
            criteria_hash=criteria_hash(specs),
        )
        run_metadata.update(dataset_lineage_metadata(dataset))
        eval_object = create_evaluation(
            openai_client,
            name=f"{suite} ASSERT precomputed {dataset.content_sha256[:12]}",
            data_source_config=dataset_schema(),
            criteria=turn_criteria(
                versions,
                specs,
                model_deployment=args.model_deployment,
            ),
            metadata=run_metadata,
        )
        result = run_dataset_evaluation(
            openai_client,
            eval_object=eval_object,
            dataset=dataset_ref,
            name=f"{suite}-{run}-precomputed",
            metadata=run_metadata,
            timeout_seconds=args.timeout_seconds,
            poll_seconds=args.poll_seconds,
        )
        print(f"Report URL: {result.report_url or '(report URL unavailable)'}")
    finally:
        for client in (openai_client, project_client, credential):
            close = getattr(client, "close", None)
            if callable(close):
                close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ArtifactError, EvaluatorError, FoundryAdapterError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
