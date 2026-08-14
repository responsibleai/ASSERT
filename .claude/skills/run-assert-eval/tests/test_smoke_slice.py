"""Tests for smoke_slice: carve a few real rows out of a generated test set.

Every test builds its own suite tree under ``tmp_path``, so nothing here reads or
writes the repo's own ``artifacts/results/``.

Run standalone:
    python -m pytest .claude/skills/run-assert-eval/tests/test_smoke_slice.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Make the skill dir importable without installing anything.
SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

import smoke_slice as ss  # noqa: E402


def _row(case_id: str, kind: str) -> str:
    return json.dumps(
        {"type": kind, "test_case_id": case_id, "behavior": "b", "dimensions": {}}
    )


def _make_suite(
    tmp_path: Path,
    suite: str = "demo-suite",
    *,
    prompts: int = 5,
    scenarios: int = 4,
    versioned: bool = True,
    publish_copy: bool = True,
    version: str = "v0001",
) -> tuple[Path, Path]:
    """Build a suite tree. Returns (results_dir, suite_root)."""

    results_dir = tmp_path / "artifacts" / "results"
    suite_root = results_dir / suite
    suite_root.mkdir(parents=True)

    lines = [_row(f"prompt_{i:03d}", "prompt") for i in range(prompts)]
    lines += [_row(f"scenario_{i:03d}", "scenario") for i in range(scenarios)]
    body = "\n".join(lines) + "\n"

    if versioned:
        artifact_dir = suite_root / ss.ARTIFACTS_DIR / "test_set" / version
        artifact_dir.mkdir(parents=True)
        (artifact_dir / ss.TEST_SET_FILE).write_text(body, encoding="utf-8")
        (suite_root / ss.LATEST_FILE).write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "artifacts": {
                        "test_set": {
                            "version": version,
                            "path": f"{ss.ARTIFACTS_DIR}/test_set/{version}/{ss.TEST_SET_FILE}",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
    if publish_copy:
        (suite_root / ss.TEST_SET_FILE).write_text(body, encoding="utf-8")

    return results_dir, suite_root


# --- resolve_test_set -------------------------------------------------------


def test_resolves_through_latest_json_not_by_guessing_v0001(tmp_path):
    """The pointer wins, so a later version dir is picked up automatically."""

    results_dir, suite_root = _make_suite(tmp_path, version="v0007", publish_copy=False)
    path, how = ss.resolve_test_set(suite_root)

    assert path.parent.name == "v0007"
    assert "latest.json" in how


def test_latest_json_wins_over_the_published_copy(tmp_path):
    results_dir, suite_root = _make_suite(tmp_path)
    # Make the published copy distinguishable from the versioned artifact.
    (suite_root / ss.TEST_SET_FILE).write_text(_row("stale_000", "prompt") + "\n", encoding="utf-8")

    path, _ = ss.resolve_test_set(suite_root)

    assert path.parent.name == "v0001"


def test_falls_back_to_published_copy_without_latest_json(tmp_path):
    results_dir, suite_root = _make_suite(tmp_path, versioned=False)
    path, how = ss.resolve_test_set(suite_root)

    assert path.name == ss.TEST_SET_FILE
    assert path.parent == suite_root
    assert "published" in how


def test_malformed_latest_json_falls_back_instead_of_crashing(tmp_path):
    results_dir, suite_root = _make_suite(tmp_path)
    (suite_root / ss.LATEST_FILE).write_text("{not json", encoding="utf-8")

    path, how = ss.resolve_test_set(suite_root)

    assert path.parent == suite_root
    assert "published" in how


def test_latest_json_pointing_outside_the_suite_is_ignored(tmp_path):
    """A path-traversal pointer must not pull in an arbitrary file."""

    results_dir, suite_root = _make_suite(tmp_path)
    (tmp_path / "evil.jsonl").write_text(_row("evil_000", "prompt") + "\n", encoding="utf-8")
    (suite_root / ss.LATEST_FILE).write_text(
        json.dumps({"artifacts": {"test_set": {"path": "../../../evil.jsonl"}}}),
        encoding="utf-8",
    )

    path, how = ss.resolve_test_set(suite_root)

    assert path.name == ss.TEST_SET_FILE
    assert "published" in how


def test_missing_suite_root_explains_how_to_generate_it(tmp_path):
    with pytest.raises(ss.SmokeSliceError) as exc:
        ss.resolve_test_set(tmp_path / "artifacts" / "results" / "nope")

    assert "inference.enabled=false" in str(exc.value)


def test_suite_without_any_test_set_is_an_error(tmp_path):
    results_dir, suite_root = _make_suite(
        tmp_path, versioned=False, publish_copy=False
    )
    with pytest.raises(ss.SmokeSliceError):
        ss.resolve_test_set(suite_root)


# --- select_rows ------------------------------------------------------------


def test_selects_only_the_requested_kind_and_reports_availability(tmp_path):
    lines = [_row("p1", "prompt"), _row("s1", "scenario"), _row("p2", "prompt")]
    selected, case_ids, available = ss.select_rows(lines, "prompt", 5)

    assert case_ids == ["p1", "p2"]
    assert available == 2
    assert len(selected) == 2


def test_takes_the_first_n_in_file_order(tmp_path):
    lines = [_row(f"p{i}", "prompt") for i in range(10)]
    _, case_ids, available = ss.select_rows(lines, "prompt", 3)

    assert case_ids == ["p0", "p1", "p2"]
    assert available == 10


def test_selected_lines_are_byte_identical_to_the_source(tmp_path):
    original = _row("p1", "prompt")
    selected, _, _ = ss.select_rows([original, _row("s1", "scenario")], "prompt", 1)

    assert selected == [original]


def test_blank_and_malformed_lines_are_skipped_not_fatal(tmp_path):
    lines = ["", "   ", "{not json", _row("p1", "prompt"), "[]"]
    selected, case_ids, available = ss.select_rows(lines, "prompt", 3)

    assert case_ids == ["p1"]
    assert available == 1
    assert len(selected) == 1


# --- build_slice ------------------------------------------------------------


def test_writes_slice_outside_the_suite_and_summarises(tmp_path):
    results_dir, suite_root = _make_suite(tmp_path)
    out = tmp_path / "smoke" / "slice.jsonl"

    summary = ss.build_slice(
        suite="demo-suite", results_dir=results_dir, count=3, out_path=out
    )

    assert summary["written"] == 3
    assert summary["available"] == 5
    assert summary["kind"] == "prompt"
    assert summary["test_case_ids"] == ["prompt_000", "prompt_001", "prompt_002"]
    assert out.read_text(encoding="utf-8").splitlines() == [
        _row("prompt_000", "prompt"),
        _row("prompt_001", "prompt"),
        _row("prompt_002", "prompt"),
    ]


def test_scenario_kind_is_supported(tmp_path):
    results_dir, _ = _make_suite(tmp_path)
    out = tmp_path / "smoke" / "scenario.jsonl"

    summary = ss.build_slice(
        suite="demo-suite", results_dir=results_dir, kind="scenario", count=2, out_path=out
    )

    assert summary["test_case_ids"] == ["scenario_000", "scenario_001"]


def test_requesting_more_than_available_writes_what_exists(tmp_path):
    results_dir, _ = _make_suite(tmp_path, prompts=2)
    out = tmp_path / "smoke" / "slice.jsonl"

    summary = ss.build_slice(
        suite="demo-suite", results_dir=results_dir, count=10, out_path=out
    )

    assert summary["requested"] == 10
    assert summary["written"] == 2


def test_refuses_to_write_inside_the_suite_root(tmp_path):
    """Writing there could clobber test_set.jsonl and invalidate the cache."""

    results_dir, suite_root = _make_suite(tmp_path)

    with pytest.raises(ss.SmokeSliceError) as exc:
        ss.build_slice(
            suite="demo-suite",
            results_dir=results_dir,
            out_path=suite_root / ss.TEST_SET_FILE,
        )

    assert "suite root" in str(exc.value)
    # The real test set is untouched.
    assert len((suite_root / ss.TEST_SET_FILE).read_text(encoding="utf-8").splitlines()) == 9


def test_default_out_path_lands_under_artifacts_smoke(tmp_path, monkeypatch):
    results_dir, _ = _make_suite(tmp_path)
    monkeypatch.setattr(ss, "_repo_root", lambda: tmp_path)

    summary = ss.build_slice(suite="demo-suite", results_dir=results_dir, count=2)

    out = Path(summary["out"])
    assert out.parent == (tmp_path / ss.ARTIFACTS_DIR / "smoke").resolve()
    assert out.name == "demo-suite-prompt-2.jsonl"


def test_missing_kind_in_test_set_is_an_actionable_error(tmp_path):
    results_dir, _ = _make_suite(tmp_path, scenarios=0)

    with pytest.raises(ss.SmokeSliceError) as exc:
        ss.build_slice(
            suite="demo-suite",
            results_dir=results_dir,
            kind="scenario",
            out_path=tmp_path / "smoke" / "x.jsonl",
        )

    assert "pipeline.test_set.scenario" in str(exc.value)


@pytest.mark.parametrize("count", [0, -1])
def test_count_must_be_positive(tmp_path, count):
    results_dir, _ = _make_suite(tmp_path)

    with pytest.raises(ss.SmokeSliceError):
        ss.build_slice(suite="demo-suite", results_dir=results_dir, count=count)


def test_unknown_kind_is_rejected(tmp_path):
    results_dir, _ = _make_suite(tmp_path)

    with pytest.raises(ss.SmokeSliceError):
        ss.build_slice(suite="demo-suite", results_dir=results_dir, kind="promt")


# --- suite is an identifier, not a path -------------------------------------


def _plant_outside_suite(tmp_path: Path) -> tuple[Path, Path]:
    """Create a readable test set outside results_dir, plus an empty results_dir."""

    outside = tmp_path / "outside"
    outside.mkdir(parents=True)
    (outside / ss.TEST_SET_FILE).write_text(
        json.dumps({"type": "prompt", "test_case_id": "leaked_001"}) + "\n",
        encoding="utf-8",
    )
    results_dir = tmp_path / "artifacts" / "results"
    results_dir.mkdir(parents=True)
    return results_dir, outside


def test_absolute_path_as_suite_is_rejected(tmp_path):
    """An absolute --suite must not be read as a suite root outside results_dir."""

    results_dir, outside = _plant_outside_suite(tmp_path)

    with pytest.raises(ss.SmokeSliceError) as exc:
        ss.build_slice(
            suite=str(outside),
            results_dir=results_dir,
            out_path=tmp_path / "smoke" / "x.jsonl",
        )

    assert "suite must" in str(exc.value)
    assert not (tmp_path / "smoke" / "x.jsonl").exists()


def test_traversal_in_suite_is_rejected(tmp_path):
    """`..` must not walk out of results_dir."""

    results_dir, _ = _plant_outside_suite(tmp_path)

    with pytest.raises(ss.SmokeSliceError) as exc:
        ss.build_slice(
            suite="../../outside",
            results_dir=results_dir,
            out_path=tmp_path / "smoke" / "y.jsonl",
        )

    assert "'..'" in str(exc.value)
    assert not (tmp_path / "smoke" / "y.jsonl").exists()


@pytest.mark.parametrize(
    "suite",
    ["", "-leading-hyphen", "has space", "has/slash", "has\\backslash", "a" * 256],
)
def test_unsafe_suite_ids_are_rejected(tmp_path, suite):
    results_dir, _ = _plant_outside_suite(tmp_path)

    # Match the validation message specifically: a plain SmokeSliceError would
    # also be raised further downstream ("suite root not found"), which would
    # let this pass without any identifier validation at all.
    with pytest.raises(ss.SmokeSliceError, match=r"suite (must|exceeds)"):
        ss.build_slice(
            suite=suite,
            results_dir=results_dir,
            out_path=tmp_path / "smoke" / "z.jsonl",
        )


def test_suite_id_validation_runs_before_any_read(tmp_path):
    """A rejected suite must not leave a default output file behind either."""

    results_dir, _ = _plant_outside_suite(tmp_path)

    with pytest.raises(ss.SmokeSliceError, match=r"suite must not contain"):
        ss.build_slice(suite="../escape", results_dir=results_dir)

    assert not (tmp_path / ss.ARTIFACTS_DIR / "smoke").exists()


# --- resolve_results_dir ----------------------------------------------------


def test_results_dir_defaults_to_artifacts_results(tmp_path):
    resolved = ss.resolve_results_dir({}, root=tmp_path)
    assert resolved == (tmp_path / "artifacts" / "results").resolve()


def test_artifacts_root_is_honoured(tmp_path):
    resolved = ss.resolve_results_dir({"artifacts_root": "out"}, root=tmp_path)
    assert resolved == (tmp_path / "out" / "results").resolve()


def test_relative_results_dir_resolves_under_artifacts_root(tmp_path):
    resolved = ss.resolve_results_dir({"results_dir": "runs"}, root=tmp_path)
    assert resolved == (tmp_path / "artifacts" / "runs").resolve()


def test_absolute_results_dir_is_used_as_is(tmp_path):
    absolute = (tmp_path / "elsewhere").resolve()
    resolved = ss.resolve_results_dir({"results_dir": str(absolute)}, root=tmp_path)
    assert resolved == absolute


def test_results_dir_with_artifacts_prefix_is_not_double_nested(tmp_path):
    """Matches assert_ai.config: `artifacts/custom` resolves under artifacts_root once.

    ASSERT strips a leading artifacts-root segment before joining, so this config
    resolves to <root>/artifacts/custom. Double-nesting it to
    <root>/artifacts/artifacts/custom would send --config looking in a tree
    ASSERT never writes to.
    """

    resolved = ss.resolve_results_dir(
        {"artifacts_root": "artifacts", "results_dir": "artifacts/custom"}, root=tmp_path
    )

    assert resolved == (tmp_path / "artifacts" / "custom").resolve()
    assert resolved != (tmp_path / "artifacts" / "artifacts" / "custom").resolve()


def test_results_dir_prefix_matches_a_renamed_artifacts_root(tmp_path):
    """The stripped segment may be the artifacts_root's own name, not just 'artifacts'."""

    resolved = ss.resolve_results_dir(
        {"artifacts_root": "out", "results_dir": "out/custom"}, root=tmp_path
    )

    assert resolved == (tmp_path / "out" / "custom").resolve()


def test_results_dir_escaping_artifacts_root_is_rejected(tmp_path):
    with pytest.raises(ss.SmokeSliceError) as exc:
        ss.resolve_results_dir({"results_dir": "../../etc"}, root=tmp_path)

    assert "escapes" in str(exc.value)


# --- CLI --------------------------------------------------------------------


def test_cli_emits_json_summary(tmp_path, capsys):
    results_dir, _ = _make_suite(tmp_path)
    out = tmp_path / "smoke" / "slice.jsonl"

    code = ss.main(
        [
            "--suite", "demo-suite",
            "--results-dir", str(results_dir),
            "--count", "2",
            "--out", str(out),
        ]
    )

    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["written"] == 2
    assert summary["suite"] == "demo-suite"


def test_cli_reports_errors_without_a_traceback(tmp_path, capsys):
    code = ss.main(["--suite", "missing", "--results-dir", str(tmp_path)])

    assert code == 1
    assert "error:" in capsys.readouterr().err


def test_cli_requires_config_or_suite(tmp_path):
    with pytest.raises(SystemExit):
        ss.main(["--count", "3"])
