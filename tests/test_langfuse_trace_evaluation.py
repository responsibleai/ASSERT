# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Deterministic regressions for the Langfuse trace example bridge."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = Path(__file__).resolve().parent.parent
BRIDGE_PATH = (
    REPO_ROOT
    / "examples"
    / "langfuse_trace_evaluation"
    / "langfuse_to_assert.py"
)


def _load_bridge():
    spec = importlib.util.spec_from_file_location(
        "langfuse_to_assert_under_test",
        BRIDGE_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bridge = _load_bridge()


def _trace(
    trace_id: str,
    timestamp: str,
    messages: list[dict[str, str]],
    assistant: str,
) -> dict:
    observation_id = f"{trace_id}-generation"
    return {
        "id": trace_id,
        "timestamp": timestamp,
        "sessionId": "session-1",
        "input": {"messages": messages},
        "output": {"role": "assistant", "content": assistant},
        "observations": [
            {
                "id": observation_id,
                "traceId": trace_id,
                "type": "GENERATION",
                "name": "answer",
                "startTime": timestamp,
                "endTime": timestamp,
                "input": {"messages": messages},
                "output": {"role": "assistant", "content": assistant},
                "metadata": None,
            }
        ],
    }


def _reconstructed_messages(traces: list[dict]) -> list[tuple[str, str]]:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        otlp_path = root / "traces.json"
        inference_path = root / "inference_set.jsonl"
        otlp = bridge.convert_traces(traces, synthesize_turns=True)
        otlp_path.write_text(json.dumps(otlp), encoding="utf-8")
        rows = bridge.emit_inference_set(
            otlp_path,
            inference_path,
            behavior="test_behavior",
        )

    assert len(rows) == 1
    messages: list[tuple[str, str]] = []
    for event in rows[0]["events"]:
        edit = event["edit"]
        if edit["type"] in ("add_message", "set_system_message"):
            message = edit["message"]
            messages.append((message["role"], message["content"]))
    return messages


def test_repeated_identical_current_turn_is_preserved() -> None:
    first = _trace(
        "trace-1",
        "2026-08-12T12:00:00Z",
        [{"role": "user", "content": "yes"}],
        "First response",
    )
    second = _trace(
        "trace-2",
        "2026-08-12T12:01:00Z",
        [{"role": "user", "content": "yes"}],
        "Second response",
    )

    assert _reconstructed_messages([second, first]) == [
        ("user", "yes"),
        ("assistant", "First response"),
        ("user", "yes"),
        ("assistant", "Second response"),
    ]


def test_overlapping_history_prefixes_and_suffixes_are_not_replayed() -> None:
    system = {"role": "system", "content": "Be concise."}
    first = _trace(
        "trace-1",
        "2026-08-12T12:00:00Z",
        [system, {"role": "user", "content": "First question"}],
        "First answer",
    )
    second = _trace(
        "trace-2",
        "2026-08-12T12:01:00Z",
        [
            system,
            {"role": "user", "content": "First question"},
            {"role": "assistant", "content": "First answer"},
            {"role": "user", "content": "Second question"},
        ],
        "Second answer",
    )
    third = _trace(
        "trace-3",
        "2026-08-12T12:02:00Z",
        [
            system,
            {"role": "assistant", "content": "Second answer"},
            {"role": "user", "content": "Third question"},
        ],
        "Third answer",
    )

    assert _reconstructed_messages([third, first, second]) == [
        ("system", "Be concise."),
        ("user", "First question"),
        ("assistant", "First answer"),
        ("user", "Second question"),
        ("assistant", "Second answer"),
        ("user", "Third question"),
        ("assistant", "Third answer"),
    ]


def test_out_of_order_trace_input_is_sorted_deterministically() -> None:
    first = _trace(
        "trace-a",
        "2026-08-12T12:00:00Z",
        [{"role": "user", "content": "A"}],
        "Answer A",
    )
    second = _trace(
        "trace-b",
        "2026-08-12T12:01:00Z",
        [{"role": "user", "content": "B"}],
        "Answer B",
    )
    expected = [
        ("user", "A"),
        ("assistant", "Answer A"),
        ("user", "B"),
        ("assistant", "Answer B"),
    ]

    assert _reconstructed_messages([second, first]) == expected
    assert _reconstructed_messages([first, second]) == expected
