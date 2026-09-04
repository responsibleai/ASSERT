# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from assert_ai.cli import cli


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _score_row(policy_violation: bool) -> dict[str, Any]:
    return {
        "judge_status": "ok",
        "target": "test-target",
        "judge_model": "test-judge",
        "verdict": {
            "dimensions": {
                "policy_violation": policy_violation,
                "overrefusal": False,
            },
            "node_judgments": [],
        },
    }


def _make_run(
    results_root: Path,
    suite_id: str,
    run_id: str,
    behavior_name: str | None,
    policy_violations: list[bool],
) -> None:
    run_dir = results_root / suite_id / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(
        json.dumps({"status": "completed", "stages": {"judge": "completed"}}),
        encoding="utf-8",
    )
    (run_dir / "config.yaml").write_text(
        "\n".join([
            "behavior:",
            f"  name: {behavior_name}",
        ]) if behavior_name is not None else "behavior: {}\n",
        encoding="utf-8",
    )
    _write_jsonl(run_dir / "scores.jsonl", [_score_row(value) for value in policy_violations])


def test_results_matrix_json_renders_two_behaviors_by_two_arms(tmp_path: Path) -> None:
    results_root = tmp_path / "results"
    _make_run(results_root, "behavior-a", "behavior-a-baseline", "behavior_a", [True, False])
    _make_run(results_root, "behavior-a", "behavior-a-prompted", "behavior_a", [False, False])
    _make_run(results_root, "behavior-b", "behavior-b-baseline", "behavior_b", [True, True])
    _make_run(results_root, "behavior-b", "behavior-b-prompted", "behavior_b", [False, True])

    result = CliRunner().invoke(
        cli,
        [
            "results",
            "matrix",
            "behavior-a/behavior-a-baseline",
            "behavior-a/behavior-a-prompted",
            "behavior-b/behavior-b-baseline",
            "behavior-b/behavior-b-prompted",
            "--results-dir",
            str(results_root),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {
        "metric": "policy_violation",
        "behaviors": ["behavior_a", "behavior_b"],
        "arms": ["baseline", "prompted"],
        "cells": {
            "behavior_a": {"baseline": 0.5, "prompted": 0.0},
            "behavior_b": {"baseline": 1.0, "prompted": 0.5},
        },
    }


def test_results_matrix_missing_cell_renders_null_and_dash(tmp_path: Path) -> None:
    results_root = tmp_path / "results"
    _make_run(results_root, "behavior-a", "behavior-a-baseline", "behavior_a", [True])
    _make_run(results_root, "behavior-a", "behavior-a-prompted", "behavior_a", [False])
    _make_run(results_root, "behavior-b", "behavior-b-baseline", "behavior_b", [False])

    args = [
        "results",
        "matrix",
        "behavior-a/behavior-a-baseline",
        "behavior-a/behavior-a-prompted",
        "behavior-b/behavior-b-baseline",
        "--results-dir",
        str(results_root),
    ]
    runner = CliRunner()

    json_result = runner.invoke(cli, [*args, "--json"])
    assert json_result.exit_code == 0, json_result.output
    payload = json.loads(json_result.output)
    assert payload["cells"]["behavior_b"]["prompted"] is None

    text_result = runner.invoke(cli, [*args, "--no-color"])
    assert text_result.exit_code == 0, text_result.output
    behavior_b_row = next(line for line in text_result.output.splitlines() if "behavior_b" in line)
    assert behavior_b_row.rstrip().endswith("-")


def test_results_matrix_suite_auto_expand_matches_explicit_args(tmp_path: Path) -> None:
    results_root = tmp_path / "results"
    _make_run(results_root, "suite-a", "suite-a-baseline", "behavior_a", [True, False])
    _make_run(results_root, "suite-a", "suite-a-prompted", "behavior_a", [False, False])

    runner = CliRunner()
    explicit = runner.invoke(
        cli,
        [
            "results",
            "matrix",
            "suite-a/suite-a-baseline",
            "suite-a/suite-a-prompted",
            "--results-dir",
            str(results_root),
            "--json",
        ],
    )
    expanded = runner.invoke(
        cli,
        [
            "results",
            "matrix",
            "--suite",
            "suite-a",
            "--results-dir",
            str(results_root),
            "--json",
        ],
    )

    assert explicit.exit_code == 0, explicit.output
    assert expanded.exit_code == 0, expanded.output
    assert json.loads(explicit.output) == json.loads(expanded.output)


def test_results_matrix_repeated_suite_expands_multiple_suites_with_known_arm_order(tmp_path: Path) -> None:
    results_root = tmp_path / "results"
    _make_run(results_root, "suite-a", "suite-a-acs", "behavior_a", [False])
    _make_run(results_root, "suite-a", "suite-a-baseline", "behavior_a", [True])
    _make_run(results_root, "suite-b", "suite-b-prompted", "behavior_b", [True, False])
    _make_run(results_root, "suite-b", "suite-b-acs", "behavior_b", [False, False])
    _make_run(results_root, "suite-b", "suite-b-baseline", "behavior_b", [True, True])

    result = CliRunner().invoke(
        cli,
        [
            "results",
            "matrix",
            "--suite",
            "suite-a",
            "--suite",
            "suite-b",
            "--results-dir",
            str(results_root),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["behaviors"] == ["behavior_a", "behavior_b"]
    assert payload["arms"] == ["baseline", "prompted", "acs"]
    assert payload["cells"] == {
        "behavior_a": {"baseline": 1.0, "prompted": None, "acs": 0.0},
        "behavior_b": {"baseline": 1.0, "prompted": 0.5, "acs": 0.0},
    }


def test_results_matrix_behavior_name_falls_back_to_suite_id(tmp_path: Path) -> None:
    results_root = tmp_path / "results"
    _make_run(results_root, "fallback-suite", "fallback-suite-baseline", None, [True])
    _make_run(results_root, "fallback-suite", "fallback-suite-prompted", None, [False])

    result = CliRunner().invoke(
        cli,
        [
            "results",
            "matrix",
            "fallback-suite/fallback-suite-baseline",
            "fallback-suite/fallback-suite-prompted",
            "--results-dir",
            str(results_root),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["behaviors"] == ["fallback-suite"]


def test_results_matrix_preserves_full_non_prefixed_run_ids(tmp_path: Path) -> None:
    results_root = tmp_path / "results"
    _make_run(
        results_root,
        "suite-a",
        "variant-c-baseline-prompt",
        "behavior_a",
        [True],
    )
    _make_run(
        results_root,
        "suite-b",
        "baseline-weak-prompt",
        "behavior_b",
        [False],
    )

    result = CliRunner().invoke(
        cli,
        [
            "results",
            "matrix",
            "suite-a/variant-c-baseline-prompt",
            "suite-b/baseline-weak-prompt",
            "--results-dir",
            str(results_root),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    arms = json.loads(result.output)["arms"]
    assert arms == ["baseline-weak-prompt", "variant-c-baseline-prompt"]
    assert "prompt" not in arms


def test_results_matrix_rejects_duplicate_behavior_arm_cells(tmp_path: Path) -> None:
    results_root = tmp_path / "results"
    _make_run(results_root, "suite-a", "suite-a-baseline", "shared_behavior", [True])
    _make_run(results_root, "suite-b", "suite-b-baseline", "shared_behavior", [False])

    result = CliRunner().invoke(
        cli,
        [
            "results",
            "matrix",
            "suite-a/suite-a-baseline",
            "suite-b/suite-b-baseline",
            "--results-dir",
            str(results_root),
            "--json",
        ],
    )

    assert result.exit_code == 1
    assert "suite-a/suite-a-baseline" in result.output
    assert "suite-b/suite-b-baseline" in result.output
    assert "behavior 'shared_behavior' and arm 'baseline'" in result.output


def test_results_matrix_rejects_unknown_metric(tmp_path: Path) -> None:
    results_root = tmp_path / "results"
    _make_run(results_root, "beh", "beh-baseline", "beh", [True])
    _make_run(results_root, "beh", "beh-prompted", "beh", [False])

    result = CliRunner().invoke(
        cli,
        [
            "results",
            "matrix",
            "--suite",
            "beh",
            "--results-dir",
            str(results_root),
            "--metric",
            "policy_violaton",
            "--json",
        ],
    )

    assert result.exit_code == 1
    assert "Metric 'policy_violaton' was not found" in result.output
    assert "policy_violation" in result.output


def test_results_matrix_malformed_config_falls_back_to_manifest(tmp_path: Path) -> None:
    results_root = tmp_path / "results"
    for run_id, flagged in (("suite-a-baseline", True), ("suite-a-prompted", False)):
        _make_run(results_root, "suite-a", run_id, None, [flagged])
        run_dir = results_root / "suite-a" / run_id
        (run_dir / "config.yaml").write_text("behavior: [\n", encoding="utf-8")
        (run_dir / "manifest.json").write_text(
            json.dumps({
                "status": "completed",
                "stages": {"judge": "completed"},
                "behavior_name": "manifest_behavior",
            }),
            encoding="utf-8",
        )

    result = CliRunner().invoke(
        cli,
        [
            "results",
            "matrix",
            "--suite",
            "suite-a",
            "--results-dir",
            str(results_root),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["behaviors"] == ["manifest_behavior"]


def test_results_matrix_deduplicates_explicit_and_suite_expansion(tmp_path: Path) -> None:
    results_root = tmp_path / "results"
    _make_run(results_root, "suite-a", "suite-a-baseline", "behavior_a", [True])
    _make_run(results_root, "suite-a", "suite-a-prompted", "behavior_a", [False])

    result = CliRunner().invoke(
        cli,
        [
            "results",
            "matrix",
            "suite-a/suite-a-baseline",
            "--suite",
            "suite-a",
            "--results-dir",
            str(results_root),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["cells"] == {
        "behavior_a": {"baseline": 1.0, "prompted": 0.0}
    }


def test_results_matrix_missing_suite_is_a_cli_error(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        cli,
        [
            "results",
            "matrix",
            "--suite",
            "missing-suite",
            "--results-dir",
            str(tmp_path / "results"),
        ],
    )

    assert result.exit_code == 1
    assert "Suite not found: missing-suite" in result.output


# --- permissibility split -------------------------------------------------
#
# `policy_violation` unions permissible and impermissible behaviors, so ranking
# behaviors by it can order them by the wrong thing entirely. The matrix
# therefore leads with the impermissible half when every run reports it.
#
# The split is derived from node judgments plus the taxonomy and is stored as a
# top-level rate, not under `dimensions`. Asking for it used to render an empty
# cell under a correct-looking "Impermissible behavior violated" heading, which
# reads as "no violations" rather than "not wired up".


def _split_score_row(*, impermissible: bool, permissible: bool) -> dict[str, Any]:
    """A row whose node judgments cover both buckets."""
    return {
        "judge_status": "ok",
        "target": "test-target",
        "judge_model": "test-judge",
        "verdict": {
            "dimensions": {
                "policy_violation": impermissible or permissible,
                "overrefusal": False,
            },
            "node_judgments": [
                {"node_index": 0, "node_name": "must never", "relevant": True, "violated": impermissible},
                {"node_index": 1, "node_name": "allowed", "relevant": True, "violated": permissible},
            ],
        },
    }


def _make_split_run(
    results_root: Path,
    suite_id: str,
    run_id: str,
    behavior_name: str,
    rows: list[tuple[bool, bool]],
    *, with_taxonomy: bool = True,
) -> None:
    run_dir = results_root / suite_id / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(
        json.dumps({"status": "completed", "stages": {"judge": "completed"}}), encoding="utf-8"
    )
    (run_dir / "config.yaml").write_text(f"behavior:\n  name: {behavior_name}\n", encoding="utf-8")
    if with_taxonomy:
        (run_dir.parent / "taxonomy.json").write_text(
            json.dumps({
                "behavior_categories": [
                    {"name": "must never", "permissible": False},
                    {"name": "allowed", "permissible": True},
                ]
            }),
            encoding="utf-8",
        )
    _write_jsonl(
        run_dir / "scores.jsonl",
        [_split_score_row(impermissible=i, permissible=p) for i, p in rows],
    )


def _set_systematize_artifact(run_dir: Path, path: str) -> None:
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest["artifact_versions"] = {
        "systematize": {
            "version": "v0001",
            "path": path,
        }
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_matrix_defaults_to_the_impermissible_half_when_every_run_has_the_split(tmp_path: Path) -> None:
    results_root = tmp_path / "results"
    # 1 of 4 impermissible, 3 of 4 permissible: the union would rank this high
    # for the wrong reason.
    _make_split_run(results_root, "beh", "beh-baseline", "beh",
                    [(True, True), (False, True), (False, True), (False, False)])
    _make_split_run(results_root, "beh", "beh-governed", "beh",
                    [(False, False), (False, False), (False, True), (False, False)])

    result = CliRunner().invoke(
        cli, ["results", "matrix", "--suite", "beh", "--results-dir", str(results_root), "--json"]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["metric"] == "policy_violation_not_permissible"
    assert payload["cells"]["beh"]["baseline"] == 0.25
    assert payload["cells"]["beh"]["governed"] == 0.0


def test_matrix_falls_back_to_union_for_all_permissible_taxonomy(tmp_path: Path) -> None:
    results_root = tmp_path / "results"
    _make_split_run(
        results_root,
        "beh",
        "beh-baseline",
        "allowed_behavior",
        [(False, True), (False, False)],
    )
    _make_split_run(
        results_root,
        "beh",
        "beh-governed",
        "allowed_behavior",
        [(False, False), (False, False)],
    )
    (results_root / "beh" / "taxonomy.json").write_text(
        json.dumps({
            "behavior_categories": [
                {"name": "allowed", "permissible": True},
            ]
        }),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        cli,
        ["results", "matrix", "--suite", "beh", "--results-dir", str(results_root), "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["metric"] == "policy_violation"
    assert payload["cells"]["allowed_behavior"] == {
        "baseline": 0.5,
        "governed": 0.0,
    }


def test_matrix_uses_each_runs_versioned_taxonomy_after_suite_reordering(tmp_path: Path) -> None:
    results_root = tmp_path / "results"
    for run_id, violated in (("beh-baseline", True), ("beh-governed", False)):
        _make_split_run(results_root, "beh", run_id, "beh", [(violated, False)])

    suite_dir = results_root / "beh"
    versioned_path = suite_dir / "artifacts" / "systematize" / "v0001" / "taxonomy.json"
    versioned_path.parent.mkdir(parents=True)
    versioned_path.write_text(
        json.dumps({
            "behavior_categories": [
                {"name": "must never", "permissible": False},
                {"name": "allowed", "permissible": True},
            ]
        }),
        encoding="utf-8",
    )
    for run_id in ("beh-baseline", "beh-governed"):
        _set_systematize_artifact(
            suite_dir / run_id,
            "artifacts/systematize/v0001/taxonomy.json",
        )

    (suite_dir / "taxonomy.json").write_text(
        json.dumps({
            "behavior_categories": [
                {"name": "allowed", "permissible": True},
                {"name": "must never", "permissible": False},
            ]
        }),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        cli,
        ["results", "matrix", "--suite", "beh", "--results-dir", str(results_root), "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["metric"] == "policy_violation_not_permissible"
    assert payload["cells"]["beh"] == {
        "baseline": 1.0,
        "governed": 0.0,
    }


def test_matrix_uses_valid_node_name_over_stale_node_index(tmp_path: Path) -> None:
    results_root = tmp_path / "results"
    for run_id, violated in (("beh-baseline", True), ("beh-governed", False)):
        _make_split_run(results_root, "beh", run_id, "beh", [(violated, False)])
        scores_path = results_root / "beh" / run_id / "scores.jsonl"
        row = _split_score_row(impermissible=violated, permissible=False)
        row["verdict"]["node_judgments"][0]["node_index"] = 1
        row["verdict"]["node_judgments"][1]["node_index"] = 0
        _write_jsonl(scores_path, [row])

    result = CliRunner().invoke(
        cli,
        ["results", "matrix", "--suite", "beh", "--results-dir", str(results_root), "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["metric"] == "policy_violation_not_permissible"
    assert payload["cells"]["beh"]["baseline"] == 1.0


def test_matrix_rejects_versioned_taxonomy_path_outside_suite(tmp_path: Path) -> None:
    results_root = tmp_path / "results"
    outside_taxonomy = results_root / "outside-taxonomy.json"
    outside_taxonomy.parent.mkdir(parents=True)
    outside_taxonomy.write_text(
        json.dumps({
            "behavior_categories": [
                {"name": "must never", "permissible": False},
                {"name": "allowed", "permissible": True},
            ]
        }),
        encoding="utf-8",
    )
    for run_id, violated in (("beh-baseline", True), ("beh-governed", False)):
        _make_split_run(
            results_root,
            "beh",
            run_id,
            "beh",
            [(violated, False)],
            with_taxonomy=False,
        )
        _set_systematize_artifact(
            results_root / "beh" / run_id,
            "../outside-taxonomy.json",
        )

    result = CliRunner().invoke(
        cli,
        ["results", "matrix", "--suite", "beh", "--results-dir", str(results_root), "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["metric"] == "policy_violation"
    assert payload["cells"]["beh"]["baseline"] == 1.0


def test_matrix_split_cells_are_populated_not_dashes(tmp_path: Path) -> None:
    """The regression: the metric resolved and labelled, but every cell was None."""
    results_root = tmp_path / "results"
    _make_split_run(results_root, "beh", "beh-baseline", "beh", [(True, False), (False, True)])
    _make_split_run(results_root, "beh", "beh-governed", "beh", [(False, False), (False, True)])

    for metric in ("policy_violation_not_permissible", "policy_violation_permissible"):
        result = CliRunner().invoke(
            cli,
            ["results", "matrix", "--suite", "beh", "--results-dir", str(results_root),
             "--metric", metric, "--json"],
        )
        assert result.exit_code == 0, result.output
        cells = json.loads(result.output)["cells"]["beh"]
        assert all(value is not None for value in cells.values()), (metric, cells)


def test_matrix_accepts_the_artifact_key_spelling_of_the_split(tmp_path: Path) -> None:
    results_root = tmp_path / "results"
    _make_split_run(results_root, "beh", "beh-baseline", "beh", [(True, False), (False, False)])
    _make_split_run(results_root, "beh", "beh-governed", "beh", [(False, False), (False, False)])

    result = CliRunner().invoke(
        cli,
        ["results", "matrix", "--suite", "beh", "--results-dir", str(results_root),
         "--metric", "not_permissible_policy_violation_rate", "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["metric"] == "policy_violation_not_permissible"
    assert payload["cells"]["beh"]["baseline"] == 0.5


def test_matrix_falls_back_to_policy_violation_without_a_taxonomy(tmp_path: Path) -> None:
    """Quality suites repurpose policy_violation and have no taxonomy; they must
    keep reporting the union rather than a table of blanks."""
    results_root = tmp_path / "results"
    _make_split_run(results_root, "beh", "beh-baseline", "beh",
                    [(True, False), (False, False)], with_taxonomy=False)
    _make_split_run(results_root, "beh", "beh-governed", "beh",
                    [(False, False), (False, False)], with_taxonomy=False)

    result = CliRunner().invoke(
        cli, ["results", "matrix", "--suite", "beh", "--results-dir", str(results_root), "--json"]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["metric"] == "policy_violation"
    assert payload["cells"]["beh"]["baseline"] == 0.5


def test_matrix_falls_back_when_taxonomy_no_longer_matches_judgments(tmp_path: Path) -> None:
    results_root = tmp_path / "results"
    for run_id, violated in (("beh-baseline", True), ("beh-governed", False)):
        _make_split_run(
            results_root,
            "beh",
            run_id,
            "beh",
            [(violated, False)],
        )
        row = _split_score_row(impermissible=violated, permissible=False)
        for index, node in enumerate(row["verdict"]["node_judgments"]):
            node["node_index"] = index + 10
            node["node_name"] = f"stale-{index}"
        _write_jsonl(results_root / "beh" / run_id / "scores.jsonl", [row])

    result = CliRunner().invoke(
        cli,
        [
            "results",
            "matrix",
            "--suite",
            "beh",
            "--results-dir",
            str(results_root),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["metric"] == "policy_violation"
    assert payload["cells"]["beh"]["baseline"] == 1.0


def test_matrix_does_not_mix_halves_when_only_some_runs_have_the_split(tmp_path: Path) -> None:
    """One run contributing an impermissible-only rate while another contributes
    the union would put non-comparable numbers in one table."""
    results_root = tmp_path / "results"
    _make_split_run(results_root, "with-tax", "with-tax-baseline", "beh_a", [(True, False)])
    _make_split_run(results_root, "no-tax", "no-tax-baseline", "beh_b",
                    [(True, False)], with_taxonomy=False)

    result = CliRunner().invoke(
        cli,
        ["results", "matrix", "with-tax/with-tax-baseline", "no-tax/no-tax-baseline",
         "--results-dir", str(results_root), "--json"],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["metric"] == "policy_violation"


def test_matrix_warns_that_split_halves_have_different_denominators(tmp_path: Path) -> None:
    """Each half is scored only over rows where its bucket was relevant, so the
    halves do not sum to policy_violation."""
    results_root = tmp_path / "results"
    _make_split_run(results_root, "beh", "beh-baseline", "beh", [(True, False), (False, True)])
    _make_split_run(results_root, "beh", "beh-governed", "beh", [(False, False), (False, True)])

    result = CliRunner().invoke(
        cli, ["results", "matrix", "--suite", "beh", "--results-dir", str(results_root)]
    )

    assert result.exit_code == 0, result.output
    assert "denominator" in result.output.lower()


# --- prompt + scenario pooling -------------------------------------------
#
# A run's prompt and scenario rows are separate metric sets. Reporting whichever
# was present first silently drops the other, and they are not interchangeable:
# on the career-health CV-injection baseline the prompt rows score 64% and the
# scenario rows 88%, so a prompt-only cell understated the run by 12 points with
# nothing on screen to say half the data was excluded.


def _mixed_run(
    results_root: Path,
    suite_id: str,
    run_id: str,
    behavior_name: str,
    prompt_flags: list[bool],
    scenario_flags: list[bool],
) -> None:
    run_dir = results_root / suite_id / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(
        json.dumps({"status": "completed", "stages": {"judge": "completed"}}), encoding="utf-8"
    )
    (run_dir / "config.yaml").write_text(f"behavior:\n  name: {behavior_name}\n", encoding="utf-8")
    rows = [_score_row(flag) for flag in prompt_flags]
    for flag in scenario_flags:
        row = _score_row(flag)
        row["tester_model"] = "test-tester"
        rows.append(row)
    _write_jsonl(run_dir / "scores.jsonl", rows)


def test_matrix_pools_prompt_and_scenario_rows(tmp_path: Path) -> None:
    results_root = tmp_path / "results"
    # 1/4 prompt + 3/4 scenario = 4/8 pooled. Prompt-only would report 0.25.
    _mixed_run(results_root, "beh", "beh-baseline", "beh",
               [True, False, False, False], [True, True, True, False])
    _mixed_run(results_root, "beh", "beh-governed", "beh",
               [False, False, False, False], [False, False, False, False])

    result = CliRunner().invoke(
        cli,
        ["results", "matrix", "--suite", "beh", "--results-dir", str(results_root),
         "--metric", "policy_violation", "--json"],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["cells"]["beh"]["baseline"] == 0.5


def test_matrix_pools_from_counts_not_by_averaging_rates(tmp_path: Path) -> None:
    """Unequal halves: the mean of the two rates is not the rate of the whole."""
    results_root = tmp_path / "results"
    # 1/1 prompt (100%) + 1/9 scenario (11.1%) = 2/10 pooled (20%).
    # Averaging the two rates would give 55.6%.
    _mixed_run(results_root, "beh", "beh-baseline", "beh",
               [True], [True] + [False] * 8)
    _mixed_run(results_root, "beh", "beh-governed", "beh", [False], [False])

    result = CliRunner().invoke(
        cli,
        ["results", "matrix", "--suite", "beh", "--results-dir", str(results_root),
         "--metric", "policy_violation", "--json"],
    )

    assert result.exit_code == 0, result.output
    baseline = json.loads(result.output)["cells"]["beh"]["baseline"]
    assert baseline == pytest.approx(0.2), baseline


def test_matrix_pools_the_permissibility_split_across_both_halves(tmp_path: Path) -> None:
    results_root = tmp_path / "results"
    run_dir = results_root / "beh" / "beh-baseline"
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(
        json.dumps({"status": "completed", "stages": {"judge": "completed"}}), encoding="utf-8"
    )
    (run_dir / "config.yaml").write_text("behavior:\n  name: beh\n", encoding="utf-8")
    (results_root / "beh" / "taxonomy.json").write_text(
        json.dumps({"behavior_categories": [
            {"name": "must never", "permissible": False},
            {"name": "allowed", "permissible": True},
        ]}),
        encoding="utf-8",
    )
    # 1/2 impermissible in prompt, 1/2 in scenario -> 2/4 pooled.
    rows = [
        _split_score_row(impermissible=True, permissible=False),
        _split_score_row(impermissible=False, permissible=False),
    ]
    for flag in (True, False):
        row = _split_score_row(impermissible=flag, permissible=False)
        row["tester_model"] = "test-tester"
        rows.append(row)
    _write_jsonl(run_dir / "scores.jsonl", rows)

    _make_split_run(results_root, "beh2", "beh2-baseline", "beh2", [(False, False)])

    result = CliRunner().invoke(
        cli,
        ["results", "matrix", "beh/beh-baseline", "beh2/beh2-baseline",
         "--results-dir", str(results_root), "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["metric"] == "policy_violation_not_permissible"
    assert payload["cells"]["beh"]["baseline"] == 0.5


def test_matrix_matches_cv_injection_prompt_scenario_totals(tmp_path: Path) -> None:
    results_root = tmp_path / "results"
    run_dir = results_root / "cv-injection" / "cv-injection-baseline"
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(
        json.dumps({"status": "completed", "stages": {"judge": "completed"}}),
        encoding="utf-8",
    )
    (run_dir / "config.yaml").write_text(
        "behavior:\n  name: cv_injection\n",
        encoding="utf-8",
    )
    (run_dir.parent / "taxonomy.json").write_text(
        json.dumps({
            "behavior_categories": [
                {"name": "must never", "permissible": False},
                {"name": "allowed", "permissible": True},
            ]
        }),
        encoding="utf-8",
    )

    rows: list[dict[str, Any]] = []
    for index in range(25):
        row = _split_score_row(
            impermissible=index < 4,
            permissible=index < 16,
        )
        if index >= 21:
            row["verdict"]["node_judgments"][0]["relevant"] = False
        rows.append(row)
    for index in range(25):
        row = _split_score_row(
            impermissible=index < 18,
            permissible=index < 22,
        )
        row["tester_model"] = "test-tester"
        rows.append(row)
    _write_jsonl(run_dir / "scores.jsonl", rows)
    _make_split_run(
        results_root,
        "control",
        "control-baseline",
        "control",
        [(False, False)],
    )

    base_args = [
        "results",
        "matrix",
        "cv-injection/cv-injection-baseline",
        "control/control-baseline",
        "--results-dir",
        str(results_root),
        "--json",
    ]
    runner = CliRunner()
    union_result = runner.invoke(cli, [*base_args, "--metric", "policy_violation"])
    split_result = runner.invoke(cli, base_args)

    assert union_result.exit_code == 0, union_result.output
    assert split_result.exit_code == 0, split_result.output
    union = json.loads(union_result.output)
    split = json.loads(split_result.output)
    assert union["cells"]["cv_injection"]["baseline"] == pytest.approx(38 / 50)
    assert split["cells"]["cv_injection"]["baseline"] == pytest.approx(22 / 46)
