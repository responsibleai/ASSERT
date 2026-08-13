# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Turn an :class:`AssertRun` into flat JSONL rows for a Foundry dataset.

The v2 exporter's central design principle: **ASSERT stays the source
of truth; Foundry becomes a viewer.** To make that work, every row we
upload carries the pre-computed ASSERT verdict alongside the
conversation. Foundry's code-based custom evaluators
(:mod:`.evaluators`) then just pluck the score off the row rather
than re-judging with a second LLM.

Each row is a flat top-level JSON object with four fields:

.. code-block:: json

    {
      "query": "Capital of Australia?",
      "response": "Canberra.",
      "assert_scores": {"policy_violation": 1.0, "overrefusal": 1.0},
      "assert_reasons": {"policy_violation": "clean", "overrefusal": "responsive"}
    }

Contract-critical: rows are FLAT top-level, NOT wrapped in
``{"item": {...}}``. Foundry wraps them internally as ``data.item.*``
when resolving ``{{item.foo}}`` mapping expressions on the evaluator
side.

Score normalization (in one place so semantics don't drift):

- Built-in violation-flag dimensions (``policy_violation``,
  ``overrefusal``) — ASSERT's boolean encodes "did a bad thing
  happen": ``True`` = bad. So ``True → 0.0`` (fail),
  ``False → 1.0`` (pass).
- Custom dimensions default to the same violation-flag convention.
  Callers with rubric-positive dimensions
  (``answer_quality: true = good``) pass ``passing_when_true={dim: True}``
  to invert.
- ``None`` / missing / non-bool → ``0.0``. An errored judge row
  should show up as a fail in Foundry, not silently pass.

The reasons carry the ASSERT judge's per-dimension justification
verbatim (``verdict.dimension_justifications[dim]``) so the Foundry
drill-in shows ASSERT's rationale, not just a bare score.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from assert_ai.integrations.foundry.artifacts import AssertRun

# ── Public constants ────────────────────────────────────────────────

# Built-in ASSERT dimensions from ``assert_ai/core/judge.py``. Both
# encode ``true = violation``; a pass is the boolean being False.
# Duplicated as a frozen set here so the row builder stays
# import-light — no need to pull in the core judge module.
_BUILT_IN_VIOLATION_DIMENSIONS = frozenset({"policy_violation", "overrefusal"})


class DatasetRowsError(ValueError):
    """Raised when a run cannot be translated into valid dataset rows."""


# ── Public API ──────────────────────────────────────────────────────


def build_dataset_rows(
    run: AssertRun,
    *,
    passing_when_true: Mapping[str, bool] | None = None,
) -> list[dict[str, Any]]:
    """Turn every scored inference row into a flat Foundry-ready dict.

    Rows are joined on ``test_case_id`` — an inference row without a
    matching scores row still emits (with empty ``assert_scores`` /
    ``assert_reasons`` maps) so Foundry can still show the
    conversation. Rows with no ``test_case_id`` are skipped (no join
    key means we cannot correlate any downstream score anyway).

    ``passing_when_true`` maps custom dimensions where ``verdict ==
    True`` is the desired outcome (e.g. ``{"answer_quality": True}``).
    Built-in dimensions cannot be overridden — they're hard-coded to
    the violation-flag convention — and passing an override for one
    raises :class:`DatasetRowsError` so a bad flag fails loudly instead
    of silently corrupting the score direction.

    Errors on empty ``inference_set``: Foundry rejects datasets with
    zero rows.
    """
    if not run.inference_set:
        raise DatasetRowsError(
            "Cannot build dataset rows from an ASSERT run with an empty "
            "inference_set. Foundry rejects datasets with zero rows."
        )

    directions = _resolve_pass_directions(passing_when_true)
    scores_by_id = _index_scores_by_test_case(run.scores)

    rows: list[dict[str, Any]] = []
    for source in run.inference_set:
        test_case_id = str(source.get("test_case_id") or "")
        if not test_case_id:
            continue
        query_msgs, response_msgs = _extract_conversation(source)
        score_row = scores_by_id.get(test_case_id, {})
        assert_scores, assert_reasons = _extract_assert_verdict(
            score_row, directions=directions
        )
        rows.append(
            {
                "query": _join_message_contents(query_msgs),
                "response": _join_message_contents(response_msgs),
                "assert_scores": assert_scores,
                "assert_reasons": assert_reasons,
            }
        )
    if not rows:
        raise DatasetRowsError(
            "Every row in the ASSERT inference_set was missing a test_case_id; "
            "no dataset rows to upload."
        )
    return rows


def rows_to_jsonl_bytes(rows: list[dict[str, Any]]) -> bytes:
    """Serialize row list to newline-delimited JSON UTF-8 bytes.

    ``ensure_ascii=False`` keeps non-ASCII customer content readable
    on the wire (Azure Blob is byte-safe regardless). Trailing
    newline matches the convention Foundry's own dataset uploads use.
    Deterministic key ordering (``sort_keys=True``) so
    :func:`content_hash` is stable across pushes with identical
    logical content.
    """
    lines = [json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows]
    return ("\n".join(lines) + "\n").encode("utf-8")


def content_hash(payload: bytes, *, length: int = 12) -> str:
    """Return the SHA-256 hex digest of ``payload`` truncated to ``length``.

    Used as the dataset asset version so identical row content ⇒
    identical version ⇒ the pipeline can reuse the existing dataset
    instead of creating a new version on every re-push. 12 hex chars
    (48 bits) is well under Foundry's 256-char version limit and
    collides at ~1-in-70M for random content, plenty for the
    per-suite version namespace.
    """
    return hashlib.sha256(payload).hexdigest()[:length]


# ── Score / reason extraction ───────────────────────────────────────


def _extract_assert_verdict(
    score_row: Mapping[str, Any],
    *,
    directions: Mapping[str, bool],
) -> tuple[dict[str, float], dict[str, str]]:
    """Return per-dimension ``(scores, reasons)`` maps for one score row.

    Score direction resolution:

    - Built-in violation-flag dims → ``not v`` (True = fail, False = pass).
    - Custom dim in ``directions`` → ``v if directions[dim] else not v``.
    - Custom dim absent from ``directions`` → default violation-flag
      (``not v``).
    - Non-bool / missing → ``0.0`` (fail).

    Reasons come from ``verdict.dimension_justifications`` verbatim;
    missing → empty string.
    """
    verdict = score_row.get("verdict")
    if not isinstance(verdict, Mapping):
        return {}, {}
    dimensions = verdict.get("dimensions")
    if not isinstance(dimensions, Mapping):
        return {}, {}
    justifications = verdict.get("dimension_justifications")
    justifications_map = (
        justifications if isinstance(justifications, Mapping) else {}
    )

    scores: dict[str, float] = {}
    reasons: dict[str, str] = {}
    for dim, raw_value in dimensions.items():
        if not isinstance(dim, str):
            continue
        scores[dim] = _normalize_dimension_score(dim, raw_value, directions)
        reason = justifications_map.get(dim)
        reasons[dim] = str(reason) if isinstance(reason, str) else ""
    return scores, reasons


def _normalize_dimension_score(
    dim: str,
    raw_value: Any,
    directions: Mapping[str, bool],
) -> float:
    """Boolean → float in [0.0, 1.0], respecting the pass direction."""
    if not isinstance(raw_value, bool):
        return 0.0
    if dim in _BUILT_IN_VIOLATION_DIMENSIONS:
        return 0.0 if raw_value else 1.0
    passing_when_true = directions.get(dim, False)
    if passing_when_true:
        return 1.0 if raw_value else 0.0
    return 0.0 if raw_value else 1.0


def _resolve_pass_directions(
    overrides: Mapping[str, bool] | None,
) -> dict[str, bool]:
    """Validate and return the pass-direction map.

    Raises :class:`DatasetRowsError` if a built-in dimension appears
    in ``overrides`` — the two built-ins are contract-fixed at
    ``true = violation`` and letting a caller flip them would silently
    invert every ASSERT run's pass counts.
    """
    result: dict[str, bool] = {}
    if not overrides:
        return result
    invalid = sorted(name for name in overrides if name in _BUILT_IN_VIOLATION_DIMENSIONS)
    if invalid:
        raise DatasetRowsError(
            "Cannot override pass-direction for built-in dimensions: "
            + ", ".join(invalid)
            + ". Built-in dimensions always use the violation-flag convention."
        )
    for name, value in overrides.items():
        if isinstance(name, str) and isinstance(value, bool):
            result[name] = value
    return result


# ── Conversation extraction ─────────────────────────────────────────


def _extract_conversation(
    row: Mapping[str, Any],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Split an inference row's events into user + assistant message lists.

    Prefers the ``combined`` view, falling back to ``target`` when
    ``combined`` is absent (matches how the viewer resolves views).
    """
    events = row.get("events")
    if not isinstance(events, list):
        return [], []

    def _in_view(event: Mapping[str, Any], view: str) -> bool:
        views = event.get("view")
        return isinstance(views, list) and view in views

    filtered = [
        e for e in events if isinstance(e, Mapping) and _in_view(e, "combined")
    ]
    if not filtered:
        filtered = [
            e for e in events if isinstance(e, Mapping) and _in_view(e, "target")
        ]

    query: list[dict[str, str]] = []
    response: list[dict[str, str]] = []
    for event in filtered:
        edit = event.get("edit")
        if not isinstance(edit, Mapping) or edit.get("type") != "add_message":
            continue
        message = edit.get("message")
        if not isinstance(message, Mapping):
            continue
        role = str(message.get("role") or "")
        content = message.get("content")
        if not isinstance(content, str) or not role:
            continue
        entry = {"role": role, "content": content}
        if role == "user":
            query.append(entry)
        elif role == "assistant":
            response.append(entry)
        # Other roles (developer, system, tool) are dropped from the
        # row; tool events are a follow-up (see the task note).
    return query, response


def _join_message_contents(messages: list[dict[str, str]]) -> str:
    """Concatenate message contents with blank lines between turns."""
    return "\n\n".join(m.get("content", "") for m in messages if m.get("content"))


def _index_scores_by_test_case(
    scores: Any,
) -> dict[str, Mapping[str, Any]]:
    """Build ``{test_case_id → score_row}`` from ``run.scores``."""
    return {
        str(row.get("test_case_id") or ""): row
        for row in scores
        if isinstance(row, Mapping) and row.get("test_case_id")
    }


__all__ = [
    "DatasetRowsError",
    "build_dataset_rows",
    "content_hash",
    "rows_to_jsonl_bytes",
]
