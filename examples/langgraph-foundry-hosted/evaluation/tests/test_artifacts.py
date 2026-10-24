# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import json
from pathlib import Path

import pytest

from artifacts import (
    ArtifactError,
    prepare_native_rows,
    prepare_precomputed_rows,
    rows_to_jsonl_bytes,
    sha256_hex,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _test_case(case_id: str, kind: str = "prompt") -> dict:
    return {
        "type": kind,
        "test_case_id": case_id,
        "seed": {"description": f"Description for {case_id}"},
        "dimensions": {"behavior": "budget", "traveler_type": "family"},
    }


def _inference(
    case_id: str,
    *,
    kind: str = "prompt",
    output: str = "Final answer",
) -> dict:
    return {
        "type": kind,
        "test_case_id": case_id,
        "behavior": "budget",
        "target": "demo",
        "tester_model": "",
        "dimensions": {"behavior": "budget", "traveler_type": "family"},
        "events": [
            {
                "view": ["target", "combined"],
                "actor": "target",
                "edit": {
                    "type": "add_message",
                    "message": {"role": "user", "content": "Plan my trip"},
                },
                "raw": {"provider_payload": "must not survive"},
            },
            {
                "view": ["target", "combined"],
                "actor": "tool",
                "edit": {
                    "type": "tool_call",
                    "tool_name": "search_hotels",
                    "tool_args": {"city": "Paris"},
                    "tool_result": '{"hotel": "Example Inn"}',
                },
                "raw": {"credential": "must not survive"},
            },
            {
                "view": ["target", "combined"],
                "actor": "target",
                "edit": {
                    "type": "add_message",
                    "message": {"role": "assistant", "content": output},
                },
            },
        ],
        "llm_calls": [{"request": {"secret": "hidden"}, "response": {"raw": True}}],
    }


def test_native_rows_split_prompt_and_scenario(tmp_path: Path) -> None:
    path = tmp_path / "test_set.jsonl"
    _write_jsonl(path, [_test_case("scenario-1", "scenario"), _test_case("prompt-1")])

    prompts, scenarios = prepare_native_rows(path, suite="suite", run="run")

    assert prompts is not None and scenarios is not None
    assert prompts.rows[0]["query"] == "Description for prompt-1"
    assert scenarios.rows[0]["test_case_description"] == "Description for scenario-1"
    assert prompts.rows[0]["assert_test_case_id"] == "prompt-1"
    assert scenarios.rows[0]["assert_dimensions"]["traveler_type"] == "family"


def test_join_is_strict_and_independent_of_input_order(tmp_path: Path) -> None:
    tests = tmp_path / "test_set.jsonl"
    inferences = tmp_path / "inference_set.jsonl"
    _write_jsonl(tests, [_test_case("b"), _test_case("a")])
    _write_jsonl(inferences, [_inference("a"), _inference("b")])

    first = prepare_precomputed_rows(tests, inferences, suite="suite", run="run")
    _write_jsonl(inferences, [_inference("b"), _inference("a")])
    second = prepare_precomputed_rows(tests, inferences, suite="suite", run="run")

    assert [row["assert_test_case_id"] for row in first.rows] == ["a", "b"]
    assert first.payload == second.payload
    assert first.content_sha256 == second.content_sha256


def test_transcript_preserves_visible_tool_evidence_and_drops_raw_payloads(tmp_path: Path) -> None:
    tests = tmp_path / "test_set.jsonl"
    inferences = tmp_path / "inference_set.jsonl"
    _write_jsonl(tests, [_test_case("a")])
    _write_jsonl(inferences, [_inference("a")])

    row = prepare_precomputed_rows(tests, inferences, suite="suite", run="run").rows[0]

    assert "search_hotels" in row["response"]
    assert "Example Inn" in row["response"]
    assert "Final answer" in row["response"]
    serialized = json.dumps(row)
    assert "provider_payload" not in serialized
    assert "llm_calls" not in serialized
    assert "credential" not in serialized


def test_scores_are_absent_and_never_read(tmp_path: Path) -> None:
    tests = tmp_path / "test_set.jsonl"
    inferences = tmp_path / "inference_set.jsonl"
    scores = tmp_path / "scores.jsonl"
    _write_jsonl(tests, [_test_case("a")])
    _write_jsonl(inferences, [_inference("a")])
    scores.write_text("not valid json and intentionally ignored", encoding="utf-8")

    result = prepare_precomputed_rows(tests, inferences, suite="suite", run="run")

    assert len(result.rows) == 1


@pytest.mark.parametrize(
    ("test_rows", "inference_rows", "message"),
    [
        ([_test_case("a"), _test_case("a")], [_inference("a")], "duplicate"),
        ([_test_case("a")], [_inference("a"), _inference("a")], "duplicate"),
        ([_test_case("a"), _test_case("b")], [_inference("a")], "missing"),
        ([_test_case("a")], [_inference("a"), _inference("b")], "unknown"),
    ],
)
def test_join_rejects_duplicate_missing_and_extra_ids(
    tmp_path: Path,
    test_rows: list[dict],
    inference_rows: list[dict],
    message: str,
) -> None:
    tests = tmp_path / "test_set.jsonl"
    inferences = tmp_path / "inference_set.jsonl"
    _write_jsonl(tests, test_rows)
    _write_jsonl(inferences, inference_rows)
    with pytest.raises(ArtifactError, match=message):
        prepare_precomputed_rows(tests, inferences, suite="suite", run="run")


def test_join_rejects_empty_target_output(tmp_path: Path) -> None:
    tests = tmp_path / "test_set.jsonl"
    inferences = tmp_path / "inference_set.jsonl"
    _write_jsonl(tests, [_test_case("a")])
    row = _inference("a", output="")
    row["events"] = row["events"][:1]
    _write_jsonl(inferences, [row])
    with pytest.raises(ArtifactError, match="no target output"):
        prepare_precomputed_rows(tests, inferences, suite="suite", run="run")


def test_rendered_transcript_redacts_local_paths_and_credentials(tmp_path: Path) -> None:
    tests = tmp_path / "test_set.jsonl"
    inferences = tmp_path / "inference_set.jsonl"
    _write_jsonl(tests, [_test_case("a")])
    local_path = "D:" + "\\" + "workspace" + "\\" + "private.txt"
    credential_value = "sensitive-" + "value"
    _write_jsonl(
        inferences,
        [_inference("a", output=f"Read {local_path} API_KEY={credential_value}")],
    )

    payload = prepare_precomputed_rows(
        tests, inferences, suite="suite", run="run"
    ).payload.decode("utf-8")

    assert "workspace" not in payload
    assert credential_value not in payload
    assert "[local path removed]" in payload
    assert "[credential removed]" in payload


def test_structured_tool_evidence_redacts_secrets_urls_and_absolute_paths(
    tmp_path: Path,
) -> None:
    tests = tmp_path / "test_set.jsonl"
    inferences = tmp_path / "inference_set.jsonl"
    _write_jsonl(tests, [_test_case("a")])
    row = _inference("a")
    row["events"][1]["edit"]["tool_args"] = {
        "api_key": "super-secret-value",
        "note": "ordinary user prose about API design",
        "callback": (
            "https://alice:password@example.test/token/"
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature"
            "?api_key=query-secret&view=summary"
        ),
        "windows_path": "C:\\workspace\\private.txt",
        "unix_path": "/workspace/private.txt",
    }
    row["events"][1]["edit"]["tool_result"] = (
        '{"password": "json-secret", "message": "keep this ordinary prose"}\n'
        "'api_key': 'yaml-secret'\n"
        "file: /srv/private/result.json"
    )
    _write_jsonl(inferences, [row])

    prepared = prepare_precomputed_rows(
        tests, inferences, suite="suite", run="run"
    )
    payload = prepared.payload.decode("utf-8")

    for secret in (
        "super-secret-value",
        "password@example",
        "query-secret",
        "json-secret",
        "yaml-secret",
        "eyJhbGciOiJIUzI1NiJ9",
    ):
        assert secret not in payload
    for local_path_part in ("C:\\\\workspace", "/workspace/private.txt", "/srv/private"):
        assert local_path_part not in payload
    assert "ordinary user prose about API design" in payload
    assert "keep this ordinary prose" in payload
    assert "view=summary" in payload


@pytest.mark.parametrize(
    ("test_row", "inference_row", "message"),
    [
        (_test_case("a", "prompt"), _inference("a", kind="scenario"), "type"),
        (
            _test_case("a"),
            {**_inference("a"), "dimensions": {"behavior": "privacy", "traveler_type": "family"}},
            "behavior",
        ),
        (
            _test_case("a"),
            {**_inference("a"), "dimensions": {"behavior": "budget", "traveler_type": "solo"}},
            "dimensions",
        ),
        (
            _test_case("a"),
            {**_inference("a"), "source_test_case_content_sha256": "0" * 64},
            "source_test_case_content_sha256",
        ),
        (
            _test_case("a"),
            {**_inference("a"), "source_test_case_id": "different-case"},
            "source_test_case_id",
        ),
    ],
)
def test_join_rejects_stale_or_mismatched_source_identity(
    tmp_path: Path,
    test_row: dict,
    inference_row: dict,
    message: str,
) -> None:
    tests = tmp_path / "test_set.jsonl"
    inferences = tmp_path / "inference_set.jsonl"
    _write_jsonl(tests, [test_row])
    _write_jsonl(inferences, [inference_row])

    with pytest.raises(ArtifactError, match=message):
        prepare_precomputed_rows(tests, inferences, suite="suite", run="run")


def test_join_accepts_matching_source_hash(tmp_path: Path) -> None:
    tests = tmp_path / "test_set.jsonl"
    inferences = tmp_path / "inference_set.jsonl"
    test_row = _test_case("a")
    inference_row = {
        **_inference("a"),
        "source_test_case_content_sha256": sha256_hex(
            rows_to_jsonl_bytes([test_row])
        ),
    }
    _write_jsonl(tests, [test_row])
    _write_jsonl(inferences, [inference_row])

    result = prepare_precomputed_rows(tests, inferences, suite="suite", run="run")

    assert len(result.rows) == 1


def test_jsonl_serialization_is_byte_deterministic() -> None:
    left = rows_to_jsonl_bytes([{"b": 1, "a": "é"}])
    right = rows_to_jsonl_bytes([{"a": "é", "b": 1}])
    assert left == right
    assert b"\xc3\xa9" in left


def test_content_hash_changes_when_source_content_changes(tmp_path: Path) -> None:
    tests = tmp_path / "test_set.jsonl"
    inferences = tmp_path / "inference_set.jsonl"
    _write_jsonl(tests, [_test_case("a")])
    _write_jsonl(inferences, [_inference("a", output="first")])
    first = prepare_precomputed_rows(tests, inferences, suite="suite", run="run")
    _write_jsonl(inferences, [_inference("a", output="second")])
    second = prepare_precomputed_rows(tests, inferences, suite="suite", run="run")
    assert first.content_sha256 != second.content_sha256
