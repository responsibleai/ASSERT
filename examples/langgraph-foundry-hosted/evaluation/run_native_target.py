# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Run Foundry-native prompt and scenario evaluation from ASSERT test cases."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from assert_ai.config import load_config, parse_pipeline_config

from artifacts import ArtifactError, PreparedDataset, prepare_native_rows
from evaluators import EvaluatorError, compile_evaluator_specs, testing_criteria
from foundry import (
    FoundryAdapterError,
    conversation_schema,
    create_clients,
    create_evaluation,
    criteria_hash,
    dataset_lineage_metadata,
    dataset_schema,
    ensure_dataset,
    ensure_evaluators,
    metadata,
    prepare_local_asset,
    run_agent_evaluation,
    run_scenario_simulation,
    turn_criteria,
)


HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE.parent / "eval_config.yaml"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate ASSERT test_set.jsonl against a Foundry agent target."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--test-set", type=Path)
    parser.add_argument("--output-dir", type=Path, default=HERE / "output")
    parser.add_argument(
        "--project-endpoint",
        default=(
            os.environ.get("AZURE_AI_PROJECT_ENDPOINT")
            or os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
        ),
    )
    parser.add_argument("--model-deployment", default=os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME"))
    parser.add_argument(
        "--simulator-model",
        default=os.environ.get("AZURE_AI_SIMULATOR_MODEL_DEPLOYMENT_NAME"),
        help="Scenario simulator deployment; defaults to --model-deployment.",
    )
    parser.add_argument("--agent-name", default=os.environ.get("FOUNDRY_AGENT_NAME"))
    parser.add_argument("--agent-version", default=os.environ.get("FOUNDRY_AGENT_VERSION"))
    parser.add_argument("--protocol", choices=("responses", "invocations"), default="responses")
    parser.add_argument("--timeout-seconds", type=float, default=1800)
    parser.add_argument("--poll-seconds", type=float, default=5)
    parser.add_argument(
        "--dry-run",
        "--prepare",
        action="store_true",
        dest="dry_run",
        help="Prepare deterministic local datasets and evaluator contracts without cloud writes.",
    )
    return parser


def _config_context(config_path: Path) -> tuple[dict[str, Any], str, str, Path, int]:
    raw = load_config(config_path)
    suite = str(raw.get("suite") or "").strip()
    run = str(raw.get("run") or "").strip()
    if not suite or not run:
        raise FoundryAdapterError("ASSERT config must define non-empty suite and run values")
    artifacts_root = (config_path.resolve().parents[2] / "artifacts" / "results").resolve()
    default_test_set = artifacts_root / suite / "test_set.jsonl"
    pipeline = parse_pipeline_config(raw)
    max_turns = (
        pipeline.evaluation.inference.max_turns
        if pipeline and pipeline.evaluation
        else 6
    )
    return raw, suite, run, default_test_set, max_turns


def _prepare_one(
    dataset: PreparedDataset,
    *,
    output_dir: Path,
    name: str,
    suite: str,
    run: str,
    route: str,
) -> tuple[Path, dict[str, str]]:
    path, _ = prepare_local_asset(
        dataset,
        output_dir=output_dir,
        dataset_name=name,
        lineage={"assert_suite": suite, "assert_run": run, "route": route},
    )
    return path, {
        "assert_suite": suite,
        "assert_run": run,
        "assert_dataset_sha256": dataset.content_sha256,
        "assert_route": route,
    }


def main() -> int:
    args = _parser().parse_args()
    config_path = args.config.resolve()
    _, suite, run, default_test_set, max_turns = _config_context(config_path)
    test_set_path = (args.test_set or default_test_set).resolve()
    prompt_data, scenario_data = prepare_native_rows(test_set_path, suite=suite, run=run)
    if prompt_data is None and scenario_data is None:
        raise FoundryAdapterError("ASSERT test_set.jsonl has no prompt or scenario rows")

    prompt_prepared = None
    scenario_prepared = None
    if prompt_data:
        prompt_prepared = _prepare_one(
            prompt_data,
            output_dir=args.output_dir,
            name=f"{suite}-assert-prompts",
            suite=suite,
            run=run,
            route="native-agent-prompt",
        )
    if scenario_data:
        scenario_prepared = _prepare_one(
            scenario_data,
            output_dir=args.output_dir,
            name=f"{suite}-assert-scenarios",
            suite=suite,
            run=run,
            route="native-agent-scenario-simulation-preview",
        )

    turn_specs = compile_evaluator_specs(config_path, level="turn") if prompt_data else []
    conversation_specs = (
        compile_evaluator_specs(config_path, level="conversation") if scenario_data else []
    )
    simulator_model = args.simulator_model or args.model_deployment
    print(
        f"Prepared {len(prompt_data.rows) if prompt_data else 0} prompt rows and "
        f"{len(scenario_data.rows) if scenario_data else 0} scenario rows."
    )
    if args.dry_run:
        print("Dry run complete: no Foundry resources were created or modified.")
        return 0

    for name, value in (
        (
            "--project-endpoint / AZURE_AI_PROJECT_ENDPOINT / FOUNDRY_PROJECT_ENDPOINT",
            args.project_endpoint,
        ),
        ("--model-deployment / AZURE_AI_MODEL_DEPLOYMENT_NAME", args.model_deployment),
        ("--agent-name / FOUNDRY_AGENT_NAME", args.agent_name),
    ):
        if not value:
            raise FoundryAdapterError(f"Cloud mode requires {name}")

    credential, project_client, openai_client = create_clients(args.project_endpoint)
    try:
        if prompt_data and prompt_prepared:
            prompt_path, prompt_lineage = prompt_prepared
            prompt_dataset = ensure_dataset(
                project_client,
                name=f"{suite}-assert-prompts",
                dataset=prompt_data,
                local_path=prompt_path,
            )
            prompt_versions = ensure_evaluators(project_client, turn_specs)
            prompt_metadata = metadata(
                suite=suite,
                run=run,
                dataset_hash=prompt_data.content_sha256,
                route="native-agent-prompt",
                criteria_hash=criteria_hash(turn_specs),
            )
            prompt_metadata.update(dataset_lineage_metadata(prompt_data))
            prompt_eval = create_evaluation(
                openai_client,
                name=f"{suite} ASSERT prompt target {prompt_data.content_sha256[:12]}",
                data_source_config=dataset_schema(include_sample_schema=True),
                criteria=turn_criteria(
                    prompt_versions,
                    turn_specs,
                    model_deployment=args.model_deployment,
                    target_generated=True,
                ),
                metadata=prompt_metadata,
            )
            result = run_agent_evaluation(
                openai_client,
                eval_object=prompt_eval,
                dataset=prompt_dataset,
                agent_name=args.agent_name,
                agent_version=args.agent_version,
                protocol=args.protocol,
                name=f"{suite}-{run}-prompt-target",
                metadata={**prompt_lineage, **prompt_metadata},
                timeout_seconds=args.timeout_seconds,
                poll_seconds=args.poll_seconds,
            )
            print(f"Prompt run report: {result.report_url or '(report URL unavailable)'}")

        if scenario_data and scenario_prepared:
            scenario_path, scenario_lineage = scenario_prepared
            scenario_dataset = ensure_dataset(
                project_client,
                name=f"{suite}-assert-scenarios",
                dataset=scenario_data,
                local_path=scenario_path,
            )
            scenario_versions = ensure_evaluators(project_client, conversation_specs)
            scenario_metadata = metadata(
                suite=suite,
                run=run,
                dataset_hash=scenario_data.content_sha256,
                route="native-agent-scenario-simulation-preview",
                criteria_hash=criteria_hash(conversation_specs),
            )
            scenario_metadata.update(dataset_lineage_metadata(scenario_data))
            scenario_eval = create_evaluation(
                openai_client,
                name=f"{suite} ASSERT scenario target {scenario_data.content_sha256[:12]}",
                data_source_config=conversation_schema(),
                criteria=testing_criteria(
                    scenario_versions,
                    conversation_specs,
                    model_deployment=args.model_deployment,
                ),
                metadata=scenario_metadata,
            )
            result = run_scenario_simulation(
                openai_client,
                eval_object=scenario_eval,
                dataset=scenario_dataset,
                agent_name=args.agent_name,
                agent_version=args.agent_version,
                simulator_model=simulator_model,
                max_turns=max_turns,
                name=f"{suite}-{run}-scenario-simulation",
                metadata={**scenario_lineage, **scenario_metadata},
                timeout_seconds=args.timeout_seconds,
                poll_seconds=args.poll_seconds,
            )
            print(f"Scenario run report: {result.report_url or '(report URL unavailable)'}")
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
