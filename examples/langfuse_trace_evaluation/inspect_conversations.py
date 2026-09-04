#!/usr/bin/env python3
"""Print the conversations ASSERT will send to the judge.

Accepts either an emitted ``inference_set.jsonl`` (recommended) or converted
OTLP JSON. Inspect the inference set before spending judge tokens.

Usage:
    python inspect_conversations.py <inference_set.jsonl|traces.json> [--full SESSION]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _assert_repo_root() -> Path:
    env = os.environ.get("ASSERT_REPO")
    if env and (Path(env) / "assert_ai").is_dir():
        return Path(env)
    for parent in Path(__file__).resolve().parents:
        if (parent / "assert_ai").is_dir():
            return parent
    raise SystemExit("Could not locate the ASSERT repo. Set ASSERT_REPO.")


sys.path.insert(0, str(_assert_repo_root()))

from assert_ai.core.otel import (  # noqa: E402
    _parse_otlp_json,
    parse_otel_traces,
    validate_spans,
)
from assert_ai.core.transcript import (  # noqa: E402
    Transcript,
    TranscriptEvent,
    TranscriptMetadata,
)


def label(edit: dict) -> str:
    if edit["type"] == "tool_call":
        return f"tool:{edit['tool_name']}"
    if edit["type"] == "set_system_message":
        return "system"
    return edit["message"]["role"]


def load_rows(path: Path, group_by: str) -> list[dict]:
    if path.suffix == ".jsonl":
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    return parse_otel_traces(path, group_by=group_by)


def row_id(row: dict, index: int) -> str:
    metadata = row.get("metadata") or {}
    return str(
        row.get("test_case_id")
        or metadata.get("session_id")
        or f"conversation-{index}"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("traces", type=Path)
    ap.add_argument("--group-by", default="session.id")
    ap.add_argument("--full", default=None, help="Render one session's judge transcript XML.")
    args = ap.parse_args()

    if args.traces.suffix != ".jsonl":
        spans = _parse_otlp_json(args.traces)
        print(f"spans          : {len(spans)}")
        print(f"span kinds     : {sorted({s.kind for s in spans})}")
        result = validate_spans(spans)
        issues = list(getattr(result, "warnings", None) or getattr(result, "issues", None) or [])
        print(f"validate_spans : {len(issues)} warning(s)")
        for warning in issues[:5]:
            print(f"  ! {warning}")

    rows = load_rows(args.traces, args.group_by)
    print(f"conversations  : {len(rows)}\n")
    for index, row in enumerate(rows):
        seq = [label(e["edit"]) for e in row["events"]]
        kind = row.get("type") or "scenario"
        print(f"  {row_id(row, index):<20} type={kind:<12} {len(seq):>2} events")
        print(f"    {' -> '.join(seq)}")

    if args.full:
        row = next(
            (candidate for index, candidate in enumerate(rows)
             if row_id(candidate, index) == args.full),
            None,
        )
        if row is None:
            raise SystemExit(f"No conversation with test_case_id={args.full}")
        test_case_id = row_id(row, rows.index(row))
        transcript = Transcript(
            metadata=TranscriptMetadata(
                kind=row.get("type") or "scenario",
                test_case_id=test_case_id,
                behavior=row.get("behavior") or "",
                target=row.get("target") or "",
                tester_model=row.get("tester_model") or "",
                dimensions={},
            ),
            events=[TranscriptEvent.model_validate(e) for e in row["events"]],
        )
        xml, _ = transcript.format_transcript_xml("target", skip_system=False)
        print("\n" + xml)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
