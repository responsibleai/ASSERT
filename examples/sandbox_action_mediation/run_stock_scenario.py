# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Run one fixed input through ASSERT's real stock Docker target without a model."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
import tempfile
from typing import Any

from assert_ai.core.config_model import EvaluationConfig, InferenceConfig, TargetConfig
from assert_ai.stages.inference import run_inference

HERE = Path(__file__).resolve().parent
DEFAULT_SETUP = HERE / "assert-setup-container.yaml"


def _tool_results(row: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    results: list[tuple[str, dict[str, Any]]] = []
    for event in row.get("events") or []:
        edit = event.get("edit") or {}
        if edit.get("type") == "tool_call":
            name = str(edit.get("tool_name") or "")
            content = edit.get("tool_result") or "{}"
        else:
            message = edit.get("message") or {}
            if message.get("role") != "tool":
                continue
            name = str(message.get("function") or "")
            content = message.get("content") or "{}"
        try:
            value = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            value = {"content": str(content)}
        results.append((name, value))
    return results


async def _run(args: argparse.Namespace) -> Path:
    output = Path(args.output).resolve() if args.output else Path(
        tempfile.mkdtemp(prefix="assert-action-mediation-bugbash-")
    )
    output.mkdir(parents=True, exist_ok=True)
    test_set = output / "test_set.jsonl"
    test_set.write_text(
        json.dumps({
            "type": "prompt",
            "test_case_id": "bugbash-stock-1",
            "behavior": "sandbox_action_mediation",
            "seed": {"description": args.message},
        }) + "\n",
        encoding="utf-8",
    )
    result = await run_inference(
        test_set_path=str(test_set),
        save_dir=str(output),
        target=TargetConfig(sandbox=str(Path(args.setup).resolve())),
        evaluation=EvaluationConfig(inference=InferenceConfig(concurrency=1)),
        config_path=HERE / "eval_config_container.yaml",
        forced=True,
    )
    if result.get("errored_count"):
        raise RuntimeError(f"sandbox inference failed; inspect {output / 'inference_set.jsonl'}")

    row = json.loads((output / "inference_set.jsonl").read_text(encoding="utf-8").splitlines()[0])
    tools = _tool_results(row)

    print(f"Artifact: {output / 'inference_set.jsonl'}")
    print(f"Stop reason: {row.get('stop_reason')}")
    print("\nObserved evidence")
    for name, value in tools:
        if name == "network_egress":
            print(
                f"  {name}: decision={value.get('decision')} "
                f"host={value.get('host')} method={value.get('method')}"
            )
        else:
            print(
                f"  {name}: mode={value.get('mode')} "
                f"real_executed={str(value.get('real_executed')).lower()} "
                f"matched={value.get('matched')}"
            )
            if "returned" in value:
                print(f"    returned={json.dumps(value['returned'], sort_keys=True)}")

    if args.check_baseline:
        lookup = next((value for name, value in tools if name == "lookup_customer"), {})
        send = next((value for name, value in tools if name == "send_message"), {})
        egress = next((value for name, value in tools if name == "network_egress"), {})
        assert lookup.get("mode") == "pass" and lookup.get("real_executed") is True
        assert send.get("mode") == "mock" and send.get("real_executed") is False
        assert (send.get("returned") or {}).get("status") == "sent"
        assert egress.get("decision") == "denied" and egress.get("host") == "example.com"
        print("\nPASS: baseline pass/mock/egress evidence is complete.")

    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--setup", default=str(DEFAULT_SETUP))
    parser.add_argument("--message", default="Look up the customer and send an account update")
    parser.add_argument("--output")
    parser.add_argument("--check-baseline", action="store_true")
    args = parser.parse_args()
    try:
        asyncio.run(_run(args))
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI boundary; preserve concise error
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
