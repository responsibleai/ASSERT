"""Inference-stage metrics computed from transcripts.

Aggregates stop-reason distribution, turn counts, truncation rates,
and conversation health indicators. All functions accept plain dicts
from transcripts.jsonl rows and return plain dicts.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from typing import Any
from p2m.core.io import row_failure_mode


def count_inference_turns(transcript_row: dict[str, Any]) -> int:
    """Count the number of tester-initiated inference turns in a transcript.

    A inference turn is an tester user-message sent to the target view.
    """
    count = 0
    for event in transcript_row.get("events", []):
        if not isinstance(event, dict):
            continue
        view = event.get("view", [])
        if isinstance(view, str):
            view = [view]
        actor = event.get("actor", "")
        edit = event.get("edit", {})
        if not isinstance(edit, dict):
            continue
        message = edit.get("message", {})
        if not isinstance(message, dict):
            continue
        role = message.get("role", "")
        if "target" in view and actor == "tester" and role == "user":
            count += 1
    return count


def compute_inference_metrics(
    transcript_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute aggregate inference metrics from transcript rows.

    Returns a dict with stop_reason distribution, turn stats, completion
    rate, and per-failure_mode breakdowns.
    """
    n = len(transcript_rows)
    if n == 0:
        return {"total": 0}

    # Stop-reason distribution
    stop_reasons: dict[str, int] = Counter()
    turn_counts: list[int] = []
    per_failure_mode: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in transcript_rows:
        sr = str(row.get("stop_reason") or "unknown")
        stop_reasons[sr] += 1
        turns = count_inference_turns(row)
        turn_counts.append(turns)
        failure_mode = (row_failure_mode(row) or "unknown")
        per_failure_mode[failure_mode].append(row)

    completed = stop_reasons.get("completed", 0) + stop_reasons.get("max_turns", 0)
    errored = stop_reasons.get("target_error", 0)
    invalid_tester = stop_reasons.get("invalid_tester_turn", 0)

    # Turn stats
    turn_mean = statistics.fmean(turn_counts) if turn_counts else 0.0
    turn_median = statistics.median(turn_counts) if turn_counts else 0.0
    # Inline p95 calculation (no numpy dependency needed)
    sorted_turns = sorted(turn_counts)
    k95 = (len(sorted_turns) - 1) * 0.95
    f95 = int(k95)
    c95 = min(f95 + 1, len(sorted_turns) - 1)
    turn_p95 = sorted_turns[f95] + (k95 - f95) * (sorted_turns[c95] - sorted_turns[f95])

    # Per-failure_mode breakdown
    failure_mode_summaries = {}
    for failure_mode, rows in sorted(per_failure_mode.items()):
        b_stop = Counter(str(r.get("stop_reason") or "unknown") for r in rows)
        b_turns = [count_inference_turns(r) for r in rows]
        failure_mode_summaries[failure_mode] = {
            "total": len(rows),
            "stop_reasons": dict(b_stop),
            "turn_mean": statistics.fmean(b_turns) if b_turns else 0.0,
            "turn_median": statistics.median(b_turns) if b_turns else 0.0,
        }

    return {
        "total": n,
        "completion_rate": completed / n,
        "error_rate": errored / n,
        "invalid_tester_rate": invalid_tester / n,
        "stop_reasons": dict(stop_reasons),
        "turns": {
            "mean": round(turn_mean, 1),
            "median": round(turn_median, 1),
            "p95": round(turn_p95, 1),
            "min": min(turn_counts),
            "max": max(turn_counts),
        },
        "by_failure_mode": failure_mode_summaries,
    }
