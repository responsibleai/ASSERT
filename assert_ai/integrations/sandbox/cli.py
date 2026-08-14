# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""CLI for the mediation setup layer.

Two commands, both aimed at the fast feedback loop a user needs while writing
mocks:

    validate  - load a setup file, report what it wires up, flag gaps
    resolve   - ask "what would this exact tool call return?" without a full eval

`resolve` matters because the failure mode of a mock file is silent: a rule that
does not match produces a *different* response rather than an error, and you find
out at judging time. This makes the resolution visible up front.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .mocks import MockCall
from .mediation_setup import SetupError, load_setup, validate_setup


def _cmd_validate(args: argparse.Namespace) -> int:
    summary = validate_setup(args.setup)
    if args.json:
        print(json.dumps(summary, indent=2))
        return 0

    print(f"setup: {summary['source']}")
    print(f"target: {summary['target']}")
    print(f"policy: {summary['policy_rules']} rules, default={summary['policy_default']}")
    print(f"mocks:  {summary['mock_rules']} rules over {len(summary['mock_tools'])} tools")
    if summary["cassette_dir"]:
        print(f"cassettes: {summary['cassette_dir']}")

    if summary["falls_back_to_inline"]:
        print("\nmocked by policy, no rule in the mock file (falls back to policy inline payload):")
        for tool in summary["falls_back_to_inline"]:
            print(f"  - {tool}")
    if summary["unused_mock_rules"]:
        print("\nin the mock file but never mocked by policy (dead content, usually a typo):")
        for tool in summary["unused_mock_rules"]:
            print(f"  - {tool}")
    if not summary["falls_back_to_inline"] and not summary["unused_mock_rules"]:
        print("\npolicy and mock file agree on every mocked tool.")
    return 0


def _cmd_resolve(args: argparse.Namespace) -> int:
    setup = load_setup(args.setup)
    try:
        call_args = json.loads(args.args) if args.args else {}
    except json.JSONDecodeError as exc:
        print(f"--args must be valid JSON: {exc}", file=sys.stderr)
        return 2
    if not isinstance(call_args, dict):
        print("--args must be a JSON object", file=sys.stderr)
        return 2

    decision = setup.policy.decide(args.tool)
    mode = str(decision.get("mode", "block"))
    print(f"tool:   {args.tool}")
    print(f"args:   {json.dumps(call_args)}")
    print(f"policy: mode={mode} (matched '{decision.get('match')}')")

    if mode not in {"mock", "inline", "replay", "poison", "inject"}:
        print("\nNot mocked by policy; the mock file is not consulted for this call.")
        return 0

    rule = setup.mocks.find(MockCall(tool=args.tool, args=call_args))
    if rule is None:
        print("\nNo mock rule matched. Falls back to the policy's inline `mock:` payload:")
        print(json.dumps(decision.get("mock"), indent=2))
        return 0

    resolution = setup.mocks.resolve(MockCall(tool=args.tool, args=call_args))
    assert resolution is not None  # find() matched, so resolve() must too
    print(f"\nmatched mock rule: tool='{rule.tool}' backend={rule.backend} when={rule.when or '(any args)'}")
    if rule.note:
        print(f"note: {rule.note}")
    print(f"provenance: {resolution.mock_source}{'  (simulated failure)' if resolution.is_error else ''}")
    if resolution.state_note:
        print(f"state: {resolution.state_note}")
    print("\nagent would receive:")
    print(json.dumps(resolution.value, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sandbox-action-mediation")
    sub = parser.add_subparsers(dest="command", required=True)

    v = sub.add_parser("validate", help="load a setup file and report what it wires up")
    v.add_argument("setup", type=Path)
    v.add_argument("--json", action="store_true", help="machine-readable output")
    v.set_defaults(func=_cmd_validate)

    r = sub.add_parser("resolve", help="show what a specific tool call would return")
    r.add_argument("setup", type=Path)
    r.add_argument("tool")
    r.add_argument("--args", help="JSON object of tool arguments", default="{}")
    r.set_defaults(func=_cmd_resolve)

    ns = parser.parse_args(argv)
    try:
        return int(ns.func(ns))
    except SetupError as exc:
        print(f"setup error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
