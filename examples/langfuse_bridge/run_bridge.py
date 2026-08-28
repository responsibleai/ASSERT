# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Export a synthetic or completed ASSERT run to Langfuse."""

from __future__ import annotations

import argparse
from pathlib import Path

from assert_ai.integrations.langfuse import LangfuseExporter, LangfuseHTTPClient


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export ASSERT-produced traces and judgments to Langfuse."
    )
    parser.add_argument(
        "--run-dir",
        type=Path,
        default=Path(__file__).parent / "sample_run",
        help="ASSERT run directory containing inference_set.jsonl and scores.jsonl.",
    )
    args = parser.parse_args()

    summary = LangfuseExporter(LangfuseHTTPClient.from_env()).export_run(
        args.run_dir
    )
    print(
        f"Exported {summary.traces_exported} trace(s) and "
        f"{summary.scores_exported} ASSERT-produced score(s) "
        f"for run {summary.run_id!r}."
    )
    if summary.not_applicable_scores:
        print(
            f"Omitted {summary.not_applicable_scores} explicitly "
            "not-applicable score(s)."
        )


if __name__ == "__main__":
    main()
