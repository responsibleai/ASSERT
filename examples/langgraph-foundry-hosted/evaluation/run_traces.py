# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Evaluate existing Foundry-connected invoke_agent telemetry without replay."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from assert_ai.config import load_config

from evaluators import EvaluatorError, compile_evaluator_specs, testing_criteria
from foundry import (
    FoundryAdapterError,
    create_clients,
    create_evaluation,
    criteria_hash,
    ensure_evaluators,
    metadata,
    run_trace_evaluation,
    trace_schema,
    turn_criteria,
)


HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE.parent / "eval_config.yaml"


def _iso8601_timestamp(value: str) -> str:
    candidate = value.strip()
    if "T" not in candidate:
        raise argparse.ArgumentTypeError(
            "must be an ISO-8601 timestamp with a time and timezone"
        )
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("must include an ISO-8601 timezone")
    return candidate


def _epoch_seconds(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be integer Unix epoch seconds") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative Unix epoch seconds")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate existing invoke_agent traces; Foundry does not invoke or replay the agent."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--level", choices=("turn", "conversation"), default="turn")
    parser.add_argument("--trace-id", action="append", default=[])
    parser.add_argument("--conversation-id", action="append", default=[])
    parser.add_argument("--agent-name", default=os.environ.get("FOUNDRY_AGENT_NAME"))
    parser.add_argument("--agent-version", default=os.environ.get("FOUNDRY_AGENT_VERSION"))
    parser.add_argument(
        "--conversation-end-time",
        type=_iso8601_timestamp,
        help="ISO-8601 upper bound for conversation-ID lookup.",
    )
    parser.add_argument(
        "--agent-start-time",
        type=_epoch_seconds,
        help="Unix epoch seconds for conversation-level agent filtering.",
    )
    parser.add_argument(
        "--agent-end-time",
        type=_epoch_seconds,
        help="Unix epoch seconds for conversation-level agent filtering.",
    )
    parser.add_argument("--lookback-hours", type=int, default=24)
    parser.add_argument("--max-traces", type=int, default=50)
    parser.add_argument(
        "--filter-strategy",
        choices=("random_sampling", "smart_filtering"),
        default="random_sampling",
    )
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


def build_trace_source(args: argparse.Namespace) -> dict[str, Any]:
    conversation_end_time = getattr(args, "conversation_end_time", None)
    agent_start_time = getattr(args, "agent_start_time", None)
    agent_end_time = getattr(args, "agent_end_time", None)
    selected = sum(bool(value) for value in (args.trace_id, args.conversation_id, args.agent_name))
    if selected != 1:
        raise FoundryAdapterError(
            "Select exactly one trace source: --trace-id, --conversation-id, or --agent-name"
        )
    if args.conversation_id:
        if args.level != "conversation":
            raise FoundryAdapterError("--conversation-id requires --level conversation")
        if agent_start_time is not None or agent_end_time is not None:
            raise FoundryAdapterError(
                "--agent-start-time/--agent-end-time require --agent-name"
            )
        source: dict[str, Any] = {
            "type": "conversation_id_source",
            "conversation_ids": args.conversation_id,
        }
        if args.lookback_hours:
            source["lookback_hours"] = args.lookback_hours
        if conversation_end_time:
            source["end_time"] = conversation_end_time
        return source
    if args.trace_id:
        if conversation_end_time or agent_start_time is not None or agent_end_time is not None:
            raise FoundryAdapterError(
                "Explicit trace IDs do not accept conversation or agent time bounds"
            )
        if args.level == "turn":
            return {
                "type": "turn_trace_ids",
                "trace_ids": args.trace_id,
                "lookback_hours": args.lookback_hours,
            }
        return {"type": "trace_id_source", "trace_ids": args.trace_id}

    if args.level == "turn":
        if conversation_end_time:
            raise FoundryAdapterError(
                "--conversation-end-time requires --conversation-id"
            )
        if agent_start_time is not None or agent_end_time is not None:
            raise FoundryAdapterError(
                "Turn-level agent trace sampling supports the documented rolling "
                "--lookback-hours window. Exact --agent-start-time/--agent-end-time "
                "windows are supported by --level conversation."
            )
        agent_id = (
            f"{args.agent_name}:{args.agent_version}"
            if args.agent_version
            else args.agent_name
        )
        return {
            "type": "turn_agent_filter",
            "agent_id": agent_id,
            "lookback_hours": args.lookback_hours,
            "max_traces": args.max_traces,
        }

    if conversation_end_time:
        raise FoundryAdapterError("--conversation-end-time requires --conversation-id")
    end_time = agent_end_time if agent_end_time is not None else int(time.time()) + 600
    start_time = (
        agent_start_time
        if agent_start_time is not None
        else end_time - args.lookback_hours * 3600
    )
    if end_time - start_time < 900:
        raise FoundryAdapterError("Agent trace time windows must be at least 15 minutes")
    source = {
        "type": "agent_filter",
        "agent_name": args.agent_name,
        "start_time": start_time,
        "end_time": end_time,
        "max_traces": args.max_traces,
        "filter_strategy": args.filter_strategy,
    }
    if args.agent_version:
        source["agent_version"] = args.agent_version
    return source


def main() -> int:
    args = _parser().parse_args()
    raw = load_config(args.config.resolve())
    suite = str(raw.get("suite") or "").strip()
    run = str(raw.get("run") or "").strip()
    if not suite or not run:
        raise FoundryAdapterError("ASSERT config must define non-empty suite and run values")
    trace_source = build_trace_source(args)
    specs = compile_evaluator_specs(args.config.resolve(), level=args.level)
    print(
        "Trace evaluation reads existing invoke_agent telemetry. It does not invoke "
        "registered external agents and does not replay requests."
    )
    if args.dry_run:
        print(json.dumps({"level": args.level, "trace_source": trace_source}, indent=2))
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
        versions = ensure_evaluators(project_client, specs)
        run_metadata = metadata(
            suite=suite,
            run=run,
            dataset_hash=None,
            route=f"trace-{args.level}",
            criteria_hash=criteria_hash(specs),
        )
        if args.level == "turn":
            criteria = turn_criteria(
                versions,
                specs,
                model_deployment=args.model_deployment,
                trace=True,
            )
        else:
            criteria = testing_criteria(
                versions,
                specs,
                model_deployment=args.model_deployment,
            )
        eval_object = create_evaluation(
            openai_client,
            name=f"{suite} ASSERT trace {args.level} {criteria_hash(specs)[:12]}",
            data_source_config=trace_schema(),
            criteria=criteria,
            metadata=run_metadata,
        )
        result = run_trace_evaluation(
            openai_client,
            eval_object=eval_object,
            level=args.level,
            trace_source=trace_source,
            name=f"{suite}-{run}-trace-{args.level}",
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
    except (EvaluatorError, FoundryAdapterError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
