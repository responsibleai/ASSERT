# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for the Foundry integration's pure ASSERT-run loader.

The loader has no network dependencies, so these tests build a minimal
run tree under ``tmp_path`` and read it back. Real ``artifacts/results/``
fixtures are not used because the smoke suite is regenerated locally
and we want a hermetic test that fails deterministically when file
naming drifts.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any

import pytest

from assert_ai.integrations.foundry.artifacts import (
    AssertRun,
    AssertRunError,
    load_run,
    viewer_file_names,
)


# ── Fixtures ─────────────────────────────────────────────────────────


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _write_text(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _build_run(root: Path) -> Path:
    """Build a complete run tree under ``root`` and return the run dir."""
    suite_dir = root / "helpful-qa"
    run_dir = suite_dir / "run-1"
    run_dir.mkdir(parents=True)

    # Suite-level artifacts.
    _write_json(
        suite_dir / "taxonomy.json",
        {
            "behavior": {
                "name": "helpful_general_qa",
                "definition": "General-knowledge factual QA.",
            },
            "behavior_categories": [
                {"name": "Concise correct factual answer", "permissible": True},
                {"name": "Calibrated uncertainty", "permissible": True},
            ],
        },
    )
    _write_json(suite_dir / "systematization.json", {"nodes": []})
    _write_json(
        suite_dir / "stratification.json",
        {
            "_metadata": {"note": "excluded from dimension count"},
            "behavior": [{"name": "cat-1"}],
            "difficulty": [{"name": "easy"}, {"name": "hard"}],
        },
    )
    _write_json(suite_dir / "suite.json", {"created_at": "2026-06-24T00:00:00Z"})
    _write_json(suite_dir / "latest.json", {"schema_version": 1, "artifacts": {}})
    _write_jsonl(
        suite_dir / "test_set.jsonl",
        [
            {
                "type": "prompt",
                "test_case_id": "test_case_000001",
                "behavior": "helpful_general_qa",
                "seed": {"description": "Capital of Australia?"},
            },
        ],
    )

    # Run-level artifacts (required).
    _write_text(
        run_dir / "config.yaml",
        textwrap.dedent(
            """
            suite: helpful-qa
            run: run-1
            behavior:
              name: helpful_general_qa
            default_model:
              name: azure/gpt-5.4-mini
            """
        ).strip()
        + "\n",
    )
    _write_jsonl(
        run_dir / "inference_set.jsonl",
        [
            {
                "type": "prompt",
                "test_case_id": "test_case_000001",
                "behavior": "helpful_general_qa",
                "events": [
                    {
                        "view": ["target", "combined"],
                        "actor": "target",
                        "edit": {
                            "type": "add_message",
                            "message": {"role": "user", "content": "Capital of Australia?"},
                        },
                    },
                    {
                        "view": ["target", "combined"],
                        "actor": "target",
                        "edit": {
                            "type": "add_message",
                            "message": {"role": "assistant", "content": "Canberra."},
                        },
                    },
                ],
            }
        ],
    )
    _write_jsonl(
        run_dir / "scores.jsonl",
        [
            {
                "type": "prompt",
                "test_case_id": "test_case_000001",
                "behavior": "helpful_general_qa",
                "judge_status": "ok",
                "verdict": {
                    "dimensions": {"policy_violation": False, "overrefusal": False},
                },
            }
        ],
    )

    # Optional artifacts.
    _write_json(
        run_dir / "metrics.json",
        {"schema_version": 1, "stages": {"inference": {"calls": 1}}},
    )
    _write_json(run_dir / "manifest.json", {"status": "completed"})
    _write_json(run_dir / "artifacts.json", {"schema_version": 1, "artifacts": {}})
    _write_text(run_dir / ".inference_config_hash", "deadbeef\n")
    _write_text(run_dir / ".judge_config_hash", "cafef00d\n")

    # Viewer bundle (all five files).
    for name in viewer_file_names():
        _write_json(run_dir / ".viewer" / name, {"file": name})

    return run_dir


# ── Happy path ───────────────────────────────────────────────────────


def test_load_run_populates_all_fields(tmp_path: Path) -> None:
    run_dir = _build_run(tmp_path)

    loaded = load_run(run_dir)

    assert isinstance(loaded, AssertRun)
    assert loaded.suite_id == "helpful-qa"
    assert loaded.run_id == "run-1"
    assert loaded.run_dir == run_dir
    assert loaded.suite_dir == run_dir.parent

    # Suite artifacts materialize.
    assert loaded.taxonomy is not None
    assert loaded.systematization == {"nodes": []}
    assert loaded.stratification is not None
    assert loaded.suite_metadata == {"created_at": "2026-06-24T00:00:00Z"}
    assert loaded.latest is not None
    assert len(loaded.test_set) == 1
    assert loaded.test_set[0]["test_case_id"] == "test_case_000001"

    # Run artifacts materialize.
    assert loaded.config["suite"] == "helpful-qa"
    assert loaded.config["default_model"]["name"] == "azure/gpt-5.4-mini"
    assert len(loaded.inference_set) == 1
    assert len(loaded.scores) == 1

    # Optional artifacts.
    assert loaded.metrics is not None
    assert loaded.manifest == {"status": "completed"}
    assert loaded.artifacts_cache is not None
    assert loaded.inference_config_hash == "deadbeef"
    assert loaded.judge_config_hash == "cafef00d"

    # Viewer bundle preserves order and paths.
    viewer = loaded.viewer_files
    assert list(viewer.keys()) == list(viewer_file_names())
    for name, path in viewer.items():
        assert path.name == name
        assert path.is_file()


def test_convenience_accessors_return_taxonomy_summary(tmp_path: Path) -> None:
    run_dir = _build_run(tmp_path)

    loaded = load_run(run_dir)

    assert loaded.behavior_name == "helpful_general_qa"
    assert loaded.behavior_definition == "General-knowledge factual QA."
    assert loaded.behavior_category_count == 2
    # `_metadata` excluded, `behavior` excluded — only `difficulty` counts.
    assert loaded.stratification_dimension_count == 1


def test_load_run_accepts_string_path(tmp_path: Path) -> None:
    run_dir = _build_run(tmp_path)

    loaded = load_run(str(run_dir))

    assert loaded.run_dir == run_dir


def test_load_run_expands_user(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_dir = _build_run(tmp_path)
    # Redirect ~ to tmp_path so ``~/helpful-qa/run-1`` resolves under tmp.
    monkeypatch.setenv("HOME", str(tmp_path))
    tilde_path = f"~/{run_dir.parent.name}/{run_dir.name}"

    loaded = load_run(tilde_path)

    assert loaded.suite_id == "helpful-qa"
    assert loaded.run_id == "run-1"


def test_assert_run_is_frozen(tmp_path: Path) -> None:
    """The dataclass is frozen so downstream consumers cannot mutate output."""
    import dataclasses

    loaded = load_run(_build_run(tmp_path))

    with pytest.raises(dataclasses.FrozenInstanceError):
        loaded.run_id = "different-run"  # type: ignore[misc]


# ── Optional artifacts ───────────────────────────────────────────────


def test_missing_optional_artifacts_load_as_none(tmp_path: Path) -> None:
    """Missing metrics / viewer bundle / hashes must not raise."""
    run_dir = _build_run(tmp_path)
    (run_dir / "metrics.json").unlink()
    (run_dir / "manifest.json").unlink()
    (run_dir / "artifacts.json").unlink()
    (run_dir / ".inference_config_hash").unlink()
    (run_dir / ".judge_config_hash").unlink()
    for viewer_file in run_dir.joinpath(".viewer").iterdir():
        viewer_file.unlink()

    loaded = load_run(run_dir)

    assert loaded.metrics is None
    assert loaded.manifest is None
    assert loaded.artifacts_cache is None
    assert loaded.inference_config_hash is None
    assert loaded.judge_config_hash is None
    assert loaded.viewer_files == {}


def test_missing_suite_metadata_loads_as_none(tmp_path: Path) -> None:
    """Missing suite-level artifacts also degrade to None (or empty tuple)."""
    run_dir = _build_run(tmp_path)
    (run_dir.parent / "taxonomy.json").unlink()
    (run_dir.parent / "test_set.jsonl").unlink()
    (run_dir.parent / "stratification.json").unlink()

    loaded = load_run(run_dir)

    assert loaded.taxonomy is None
    assert loaded.test_set == ()
    assert loaded.stratification is None
    assert loaded.behavior_name is None
    assert loaded.behavior_definition is None
    assert loaded.behavior_category_count == 0
    assert loaded.stratification_dimension_count == 0


def test_empty_hash_file_loads_as_none(tmp_path: Path) -> None:
    run_dir = _build_run(tmp_path)
    (run_dir / ".judge_config_hash").write_text("   \n", encoding="utf-8")

    loaded = load_run(run_dir)

    assert loaded.judge_config_hash is None


# ── Failure modes ────────────────────────────────────────────────────


def test_missing_run_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(AssertRunError, match="does not exist"):
        load_run(tmp_path / "no-such-run")


@pytest.mark.parametrize(
    "required_file",
    ["config.yaml", "inference_set.jsonl", "scores.jsonl"],
)
def test_missing_required_artifact_raises(tmp_path: Path, required_file: str) -> None:
    run_dir = _build_run(tmp_path)
    (run_dir / required_file).unlink()

    with pytest.raises(AssertRunError, match=required_file):
        load_run(run_dir)


def test_malformed_jsonl_row_raises_with_line_number(tmp_path: Path) -> None:
    run_dir = _build_run(tmp_path)
    # Append a broken line to scores.jsonl so line 2 is malformed.
    with (run_dir / "scores.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("not-json\n")

    with pytest.raises(AssertRunError, match=r"scores\.jsonl:2"):
        load_run(run_dir)


def test_malformed_json_object_raises(tmp_path: Path) -> None:
    run_dir = _build_run(tmp_path)
    (run_dir / "manifest.json").write_text("{bad json", encoding="utf-8")

    with pytest.raises(AssertRunError, match="Malformed JSON"):
        load_run(run_dir)


def test_non_object_json_payload_raises(tmp_path: Path) -> None:
    run_dir = _build_run(tmp_path)
    (run_dir / "manifest.json").write_text("[1, 2, 3]", encoding="utf-8")

    with pytest.raises(AssertRunError, match="Expected a JSON object"):
        load_run(run_dir)


def test_non_object_jsonl_row_raises(tmp_path: Path) -> None:
    run_dir = _build_run(tmp_path)
    with (run_dir / "inference_set.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("[1, 2, 3]\n")

    with pytest.raises(AssertRunError, match="Expected an object"):
        load_run(run_dir)


def test_malformed_yaml_config_raises(tmp_path: Path) -> None:
    run_dir = _build_run(tmp_path)
    (run_dir / "config.yaml").write_text(":\n  - unbalanced\n    [", encoding="utf-8")

    with pytest.raises(AssertRunError, match="Malformed YAML"):
        load_run(run_dir)


def test_non_mapping_yaml_config_raises(tmp_path: Path) -> None:
    run_dir = _build_run(tmp_path)
    (run_dir / "config.yaml").write_text("- just\n- a\n- list\n", encoding="utf-8")

    with pytest.raises(AssertRunError, match="Expected a mapping"):
        load_run(run_dir)


# ── Lazy loading ─────────────────────────────────────────────────────


def test_lazy_load_via_package_root() -> None:
    """`from assert_ai.integrations.foundry import load_run` works."""
    import assert_ai.integrations.foundry as foundry

    assert foundry.load_run is load_run
    assert foundry.AssertRun is AssertRun
    assert foundry.AssertRunError is AssertRunError
