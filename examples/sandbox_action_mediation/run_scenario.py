# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Run one deterministic action-mediation scenario without a model or Docker.

This is the fast bug-bash path. The scenario declares the action in YAML, the
setup file binds policy + mock fidelity, and the real send implementation raises
if it is ever reached. A successful run therefore proves both containment and
evidence clarity; it cannot pass by accidentally performing the side effect.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

from assert_ai.integrations.sandbox import load_setup
from assert_ai.integrations.sandbox.evidence import assert_tool_event

HERE = Path(__file__).resolve().parent
DEFAULT_SETUP = HERE / "assert-setup.yaml"
DEFAULT_SCENARIO = HERE / "scenario.yaml"


def _load_scenario(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("scenario file must be a YAML mapping")
    if data.get("version", 1) != 1:
        raise ValueError(f"unsupported scenario version: {data.get('version')!r}")
    tool = str(data.get("tool") or "").strip()
    if not tool:
        raise ValueError("scenario requires `tool:`")
    args = data.get("args")
    if not isinstance(args, dict):
        raise ValueError("scenario requires an `args:` mapping")
    return data


def run_scenario(*, setup_path: Path, scenario_path: Path) -> dict[str, Any]:
    setup = load_setup(setup_path)
    scenario = _load_scenario(scenario_path)
    tool = str(scenario["tool"])
    args = dict(scenario["args"])

    def irreversible_send(_args: dict[str, Any]) -> Any:
        raise RuntimeError(
            "CONTAINMENT FAILURE: the real send_message implementation executed; "
            "this would contact an outside-world recipient"
        )

    host = setup.tool_host(
        tools={tool: irreversible_send},
        agent_id="bug-bash-agent",
        session_id=str(scenario.get("name") or "bug-bash-scenario"),
        framework="assert-bug-bash",
    )
    agent_received = host.call_tool(tool, args)
    record = host.records[-1]
    decision = record.decision
    event = assert_tool_event(record)

    if decision.mode not in {"mock", "block"}:
        raise RuntimeError(
            f"CONTAINMENT FAILURE: expected mock or block, got {decision.mode!r}"
        )
    if decision.real_executed:
        raise RuntimeError("CONTAINMENT FAILURE: evidence says the real tool executed")
    if record.args != args:
        raise RuntimeError("EVIDENCE FAILURE: recorded arguments differ from the attempted action")
    if event.get("tool_name") != tool or event.get("tool_args") != args:
        raise RuntimeError("EVIDENCE FAILURE: ASSERT event lost the attempted tool or arguments")

    mediation = decision.evidence()
    raw_replay = mediation.get("replay")
    replay: dict[str, Any] = raw_replay if isinstance(raw_replay, dict) else {}
    return {
        "scenario": str(scenario.get("name") or scenario_path.stem),
        "description": str(scenario.get("description") or ""),
        "attempted": {"tool": tool, "args": args},
        "outcome": {
            "mode": decision.mode,
            "real_executed": decision.real_executed,
            "flagged": decision.flagged,
            "agent_received": agent_received,
        },
        "evidence": {
            "role": event.get("role"),
            "tool_name": event.get("tool_name"),
            "tool_args": event.get("tool_args"),
            "tool_call_id": event.get("tool_call_id"),
            "matched_policy": decision.matched,
            "mock_rule": replay.get("mock_rule"),
            "matched_args": replay.get("matched_args") or [],
        },
        "pass": True,
    }


def _print_human(result: dict[str, Any]) -> None:
    attempted = result["attempted"]
    outcome = result["outcome"]
    evidence = result["evidence"]
    print(f"Scenario: {result['scenario']}")
    print(f"\nAttempted action\n  tool: {attempted['tool']}\n  args: {json.dumps(attempted['args'], sort_keys=True)}")
    print(
        "\nActual outcome"
        f"\n  policy decision: {outcome['mode']}"
        f"\n  real tool executed: {'YES' if outcome['real_executed'] else 'no'}"
    )
    print(f"\nWhat the agent received\n  {json.dumps(outcome['agent_received'], sort_keys=True)}")
    print(
        "\nJudge evidence"
        f"\n  tool: {evidence['tool_name']}"
        f"\n  args preserved: {'yes' if evidence['tool_args'] == attempted['args'] else 'NO'}"
        f"\n  policy rule: {evidence['matched_policy']}"
        f"\n  mock rule: {evidence['mock_rule'] or '(not consulted)'}"
        f"\n  matched args: {', '.join(evidence['matched_args']) or '(none)'}"
    )
    print("\nPASS: risky action was contained and the attempted action is judge-visible.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--setup", type=Path, default=DEFAULT_SETUP)
    parser.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO)
    parser.add_argument("--expect", choices=["mock", "block"], help="fail if policy selects another mode")
    parser.add_argument("--json", action="store_true", help="print machine-readable result")
    args = parser.parse_args(argv)

    result = run_scenario(setup_path=args.setup, scenario_path=args.scenario)
    if args.expect and result["outcome"]["mode"] != args.expect:
        print(
            f"expected mode {args.expect!r}, got {result['outcome']['mode']!r}",
            flush=True,
        )
        return 2
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        _print_human(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
