# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for the Foundry dataset row builder + JSONL serializer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from assert_ai.integrations.foundry.artifacts import AssertRun
from assert_ai.integrations.foundry.dataset import (
    DatasetRowsError,
    build_dataset_rows,
    content_hash,
    rows_to_jsonl_bytes,
)


# ── Fixtures ─────────────────────────────────────────────────────────


_UNSET: Any = object()


def _make_run(
    *,
    inference_rows: Any = _UNSET,
    score_rows: Any = _UNSET,
) -> AssertRun:
    default_inference = [
        {
            "type": "prompt",
            "test_case_id": "tc-1",
            "behavior": "b",
            "events": [
                {
                    "view": ["target", "combined"],
                    "edit": {
                        "type": "add_message",
                        "message": {"role": "user", "content": "Hi"},
                    },
                },
                {
                    "view": ["target", "combined"],
                    "edit": {
                        "type": "add_message",
                        "message": {"role": "assistant", "content": "Hello!"},
                    },
                },
            ],
        }
    ]
    default_scores = [
        {
            "test_case_id": "tc-1",
            "judge_model": "azure/gpt-5.4-mini",
            "judge_status": "ok",
            "verdict": {
                "dimensions": {"policy_violation": False, "overrefusal": False},
                "dimension_justifications": {
                    "policy_violation": "clean",
                    "overrefusal": "responsive",
                },
            },
        }
    ]
    return AssertRun(
        run_dir=Path("/tmp/r"),
        suite_dir=Path("/tmp/s"),
        suite_id="s",
        run_id="r",
        taxonomy=None,
        systematization=None,
        stratification=None,
        suite_metadata=None,
        latest=None,
        test_set=(),
        config={},
        inference_set=tuple(default_inference if inference_rows is _UNSET else inference_rows),
        scores=tuple(default_scores if score_rows is _UNSET else score_rows),
        metrics=None,
        manifest=None,
        artifacts_cache=None,
        inference_config_hash=None,
        judge_config_hash=None,
        viewer_files={},
    )


# ── build_dataset_rows — happy path ─────────────────────────────────


def test_row_has_flat_top_level_fields() -> None:
    """Contract: rows MUST be flat, NOT wrapped in {"item": {...}}."""
    rows = build_dataset_rows(_make_run())

    assert len(rows) == 1
    row = rows[0]
    assert "item" not in row
    assert set(row.keys()) == {"query", "response", "assert_scores", "assert_reasons"}


def test_query_and_response_extracted_from_events() -> None:
    rows = build_dataset_rows(_make_run())

    assert rows[0]["query"] == "Hi"
    assert rows[0]["response"] == "Hello!"


def test_multi_turn_conversation_joins_with_blank_line() -> None:
    inference = [
        {
            "type": "prompt",
            "test_case_id": "tc-1",
            "events": [
                {"view": ["combined"], "edit": {"type": "add_message", "message": {"role": "user", "content": "Hi"}}},
                {"view": ["combined"], "edit": {"type": "add_message", "message": {"role": "assistant", "content": "Hello!"}}},
                {"view": ["combined"], "edit": {"type": "add_message", "message": {"role": "user", "content": "How are you?"}}},
                {"view": ["combined"], "edit": {"type": "add_message", "message": {"role": "assistant", "content": "Good, thanks."}}},
            ],
        }
    ]

    rows = build_dataset_rows(_make_run(inference_rows=inference))

    assert rows[0]["query"] == "Hi\n\nHow are you?"
    assert rows[0]["response"] == "Hello!\n\nGood, thanks."


def test_rows_skip_inference_without_test_case_id() -> None:
    inference = [
        {"type": "prompt", "events": []},  # no test_case_id → skip
        {
            "type": "prompt",
            "test_case_id": "tc-keep",
            "events": [
                {"view": ["combined"], "edit": {"type": "add_message", "message": {"role": "user", "content": "keep"}}},
            ],
        },
    ]

    rows = build_dataset_rows(_make_run(inference_rows=inference))

    assert len(rows) == 1
    assert rows[0]["query"] == "keep"


def test_conversation_falls_back_to_target_view_when_no_combined() -> None:
    inference = [
        {
            "type": "prompt",
            "test_case_id": "tc-1",
            "events": [
                {"view": ["target"], "edit": {"type": "add_message", "message": {"role": "user", "content": "target-only"}}},
                {"view": ["target"], "edit": {"type": "add_message", "message": {"role": "assistant", "content": "reply"}}},
            ],
        }
    ]

    rows = build_dataset_rows(_make_run(inference_rows=inference))

    assert rows[0]["query"] == "target-only"
    assert rows[0]["response"] == "reply"


# ── Score normalization ─────────────────────────────────────────────


def test_built_in_dims_use_violation_flag_convention() -> None:
    """Built-in dims: True (violation) → 0.0, False (clean) → 1.0."""
    scores = [
        {
            "test_case_id": "tc-1",
            "judge_status": "ok",
            "verdict": {
                "dimensions": {"policy_violation": True, "overrefusal": False},
                "dimension_justifications": {"policy_violation": "bad", "overrefusal": "clean"},
            },
        }
    ]

    rows = build_dataset_rows(_make_run(score_rows=scores))

    assert rows[0]["assert_scores"] == {"policy_violation": 0.0, "overrefusal": 1.0}


def test_custom_dim_defaults_to_violation_flag() -> None:
    """Custom dims default to violation-flag (matches built-in convention)."""
    scores = [
        {
            "test_case_id": "tc-1",
            "judge_status": "ok",
            "verdict": {
                "dimensions": {"policy_violation": False, "answer_quality": True},
            },
        }
    ]

    rows = build_dataset_rows(_make_run(score_rows=scores))

    # answer_quality=True → 0.0 by default (violation-flag convention).
    assert rows[0]["assert_scores"] == {"policy_violation": 1.0, "answer_quality": 0.0}


def test_custom_dim_pass_direction_can_be_overridden() -> None:
    """`passing_when_true={dim: True}` flips a rubric-positive custom dim."""
    scores = [
        {
            "test_case_id": "tc-1",
            "judge_status": "ok",
            "verdict": {
                "dimensions": {"policy_violation": False, "answer_quality": True},
            },
        }
    ]

    rows = build_dataset_rows(
        _make_run(score_rows=scores),
        passing_when_true={"answer_quality": True},
    )

    # Now True = pass = 1.0.
    assert rows[0]["assert_scores"]["answer_quality"] == 1.0


def test_built_in_override_raises() -> None:
    """Built-in dims are contract-fixed; overriding must fail loudly."""
    with pytest.raises(DatasetRowsError, match="policy_violation"):
        build_dataset_rows(_make_run(), passing_when_true={"policy_violation": True})


def test_non_bool_value_normalizes_to_zero() -> None:
    scores = [
        {
            "test_case_id": "tc-1",
            "judge_status": "ok",
            "verdict": {
                "dimensions": {"policy_violation": None, "overrefusal": "weird"},
            },
        }
    ]

    rows = build_dataset_rows(_make_run(score_rows=scores))

    assert rows[0]["assert_scores"] == {"policy_violation": 0.0, "overrefusal": 0.0}


def test_reasons_include_justifications_verbatim() -> None:
    scores = [
        {
            "test_case_id": "tc-1",
            "judge_status": "ok",
            "verdict": {
                "dimensions": {"policy_violation": False, "overrefusal": False},
                "dimension_justifications": {
                    "policy_violation": "no relevant behavior violated",
                    "overrefusal": "assistant answered directly",
                },
            },
        }
    ]

    rows = build_dataset_rows(_make_run(score_rows=scores))

    assert rows[0]["assert_reasons"] == {
        "policy_violation": "no relevant behavior violated",
        "overrefusal": "assistant answered directly",
    }


def test_reasons_default_to_empty_when_missing() -> None:
    scores = [
        {
            "test_case_id": "tc-1",
            "judge_status": "ok",
            "verdict": {"dimensions": {"policy_violation": False}},
        }
    ]

    rows = build_dataset_rows(_make_run(score_rows=scores))

    assert rows[0]["assert_reasons"] == {"policy_violation": ""}


def test_row_without_matching_score_still_emits() -> None:
    """Inference row survives even without a matching scores row — empty maps."""
    inference = [
        {
            "type": "prompt",
            "test_case_id": "tc-no-score",
            "events": [
                {"view": ["combined"], "edit": {"type": "add_message", "message": {"role": "user", "content": "Hi"}}},
                {"view": ["combined"], "edit": {"type": "add_message", "message": {"role": "assistant", "content": "Hello"}}},
            ],
        }
    ]

    rows = build_dataset_rows(_make_run(inference_rows=inference))

    assert rows[0]["query"] == "Hi"
    assert rows[0]["assert_scores"] == {}
    assert rows[0]["assert_reasons"] == {}


# ── Error paths ─────────────────────────────────────────────────────


def test_empty_inference_set_raises() -> None:
    with pytest.raises(DatasetRowsError, match="empty inference_set"):
        build_dataset_rows(_make_run(inference_rows=[]))


def test_inference_with_only_id_less_rows_raises() -> None:
    inference = [
        {"type": "prompt", "events": []},
        {"type": "prompt", "events": []},
    ]

    with pytest.raises(DatasetRowsError, match="no dataset rows"):
        build_dataset_rows(_make_run(inference_rows=inference))


# ── rows_to_jsonl_bytes ─────────────────────────────────────────────


def test_serialize_produces_newline_delimited_bytes() -> None:
    rows = [
        {"query": "a", "response": "b", "assert_scores": {}, "assert_reasons": {}},
        {"query": "c", "response": "d", "assert_scores": {}, "assert_reasons": {}},
    ]

    payload = rows_to_jsonl_bytes(rows)

    lines = payload.decode("utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["query"] == "a"
    assert json.loads(lines[1])["query"] == "c"
    # Trailing newline.
    assert payload.endswith(b"\n")


def test_serialize_preserves_non_ascii() -> None:
    rows = [{"query": "日本語", "response": "хай", "assert_scores": {}, "assert_reasons": {}}]

    payload = rows_to_jsonl_bytes(rows)

    # ensure_ascii=False keeps the source characters intact.
    assert "日本語".encode("utf-8") in payload
    assert "хай".encode("utf-8") in payload


def test_serialize_is_deterministic_across_key_order() -> None:
    """sort_keys=True means identical logical content ⇒ identical bytes."""
    rows_a = [{"query": "a", "response": "b", "assert_scores": {"x": 1.0, "y": 0.0}, "assert_reasons": {}}]
    rows_b = [{"assert_reasons": {}, "response": "b", "assert_scores": {"y": 0.0, "x": 1.0}, "query": "a"}]

    assert rows_to_jsonl_bytes(rows_a) == rows_to_jsonl_bytes(rows_b)


# ── content_hash ────────────────────────────────────────────────────


def test_content_hash_is_stable() -> None:
    payload = b'{"query": "a"}\n'

    assert content_hash(payload) == content_hash(payload)


def test_content_hash_default_length_is_twelve() -> None:
    payload = b"anything"

    result = content_hash(payload)

    assert len(result) == 12
    # Hex chars only.
    assert all(c in "0123456789abcdef" for c in result)


def test_content_hash_length_configurable() -> None:
    payload = b"anything"

    assert len(content_hash(payload, length=8)) == 8
    assert len(content_hash(payload, length=32)) == 32


def test_content_hash_changes_when_content_changes() -> None:
    assert content_hash(b"a") != content_hash(b"b")


# ── End-to-end determinism (build + serialize + hash) ──────────────


def test_end_to_end_deterministic() -> None:
    """Two builds of the same run ⇒ same content hash. This is the version-reuse contract."""
    run = _make_run()
    b1 = rows_to_jsonl_bytes(build_dataset_rows(run))
    b2 = rows_to_jsonl_bytes(build_dataset_rows(run))

    assert content_hash(b1) == content_hash(b2)


# ── Lazy load via package root ──────────────────────────────────────


def test_lazy_load_via_package_root() -> None:
    import assert_ai.integrations.foundry as foundry

    assert foundry.build_dataset_rows is build_dataset_rows
    assert foundry.rows_to_jsonl_bytes is rows_to_jsonl_bytes
    assert foundry.content_hash is content_hash
    assert foundry.DatasetRowsError is DatasetRowsError
