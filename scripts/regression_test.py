# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""PR regression test — placeholder stub.

The full implementation (defined in ``.github/copilot-instructions.md``)
will run the pipeline on both ``tests/regression/config_{safety,quality}.yaml``
at the baseline and treatment commits, compute science efficacy metrics,
run a paired t-test via ``scipy.stats.ttest_rel``, and gate the PR on the
result.

Until that lands, this stub writes a minimal ``regression_report.json``
that the CI workflow's "Post results to PR summary" step can consume and
exits 0 so PRs aren't blocked. Replace this file with the real
implementation; do not extend the stub.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, help="Baseline commit SHA")
    parser.add_argument("--treatment", required=True, help="Treatment commit SHA")
    parser.add_argument("--test_set", type=int, default=50, help="Test Set per risk spec")
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=Path("artifacts/regression"),
        help="Directory for the regression report",
    )
    return parser.parse_args(argv)


def _build_report(args: argparse.Namespace) -> dict:
    return {
        "schema_version": 0,
        "status": "not_implemented",
        "baseline_sha": args.baseline,
        "treatment_sha": args.treatment,
        "test_cases_per_spec": args.test_set,
        "decision": {
            "decision": "PASS",
            "reasons": [
                "regression_test.py is a placeholder; no real comparison was run.",
                "See .github/copilot-instructions.md → 'Pipeline Science Efficacy' "
                "for the metrics this script must compute once implemented.",
            ],
        },
        "results": [
            {
                "metric_name": "regression_harness",
                "effect": "Info",
                "detail": "stub — PR gate is a no-op until full implementation lands",
            }
        ],
    }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    args.artifacts_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.artifacts_dir / "regression_report.json"
    report_path.write_text(json.dumps(_build_report(args), indent=2), encoding="utf-8")
    print(f"[regression_test stub] wrote placeholder report to {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
