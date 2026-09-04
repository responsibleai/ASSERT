#!/usr/bin/env python3
"""Verify the Langfuse example against the installed ASSERT parser."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "fixtures"))

import langfuse_to_assert as bridge  # noqa: E402
from build_helix_corpus import SESSIONS, build_corpus  # noqa: E402


def event_label(event: dict) -> str:
    edit = event["edit"]
    if edit["type"] == "tool_call":
        return f"tool:{edit['tool_name']}"
    if edit["type"] == "set_system_message":
        return "system"
    return edit["message"]["role"]


def main() -> int:
    traces = build_corpus()
    assert len(traces) == 20
    assert len({trace["sessionId"] for trace in traces}) == 8
    assert sum(len(trace["observations"]) for trace in traces) == 80

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        otlp_path = root / "traces.json"
        inference_path = root / "inference_set.jsonl"
        otlp = bridge.convert_traces(traces)
        otlp_path.write_text(json.dumps(otlp, ensure_ascii=False), encoding="utf-8")
        rows = bridge.emit_inference_set(
            otlp_path,
            inference_path,
            behavior="helix_docs_assistant_policy",
        )

    assert len(rows) == 8
    rows_by_id = {row["test_case_id"]: row for row in rows}
    assert set(rows_by_id) == {session["session_id"] for session in SESSIONS}

    for session in SESSIONS:
        row = rows_by_id[session["session_id"]]
        labels = [event_label(event) for event in row["events"]]
        assert labels[0:2] == ["system", "user"], labels
        assert labels.count("user") == len(session["turns"]), labels
        assert labels.count("assistant") == len(session["turns"]), labels
        assert labels.count("tool:search_docs") == len(session["turns"]), labels

    taxonomy = json.loads((HERE / "taxonomy.json").read_text(encoding="utf-8"))
    categories = taxonomy["behavior_categories"]
    assert len(categories) == 7
    assert sum(category["permissible"] is False for category in categories) == 6
    assert sum(category["permissible"] is True for category in categories) == 1
    assert all("permissible" in category for category in categories)

    print("PASS: 20 traces -> 8 ordered conversations")
    print("PASS: every user, assistant, and search_docs turn appears exactly once")
    print("PASS: taxonomy has 6 impermissible and 1 permissible category")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
