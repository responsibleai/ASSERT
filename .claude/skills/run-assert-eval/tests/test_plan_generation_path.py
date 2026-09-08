"""Tests for plan_generation_path: path-only generation directory planning.

Every test builds its own generation root under ``tmp_path``, so nothing here
reads or writes the repo's own ``examples/`` directory.

Run standalone:
    python -m pytest .claude/skills/run-assert-eval/tests/test_plan_generation_path.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Make the skill dir importable without installing anything.
SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

import plan_generation_path as pgp  # noqa: E402


def test_no_prior_generation_proposes_unsuffixed_path(tmp_path):
    root = tmp_path / "examples"

    plan = pgp.plan_generation(
        eval_type="harm", name="checkout_risk", root=root, run_date="2026-01-02"
    )

    assert plan["prior_generation_directories"] == []
    assert plan["requires_confirmation"] is False
    assert Path(plan["proposed_directory"]) == root / "checkout_risk"
    assert plan["uses_date_suffix"] is False


def test_prior_directory_with_yaml_requires_confirmation_and_new_dated_path(tmp_path):
    root = tmp_path / "examples"
    prior = root / "checkout_risk"
    prior.mkdir(parents=True)
    (prior / "eval_config.yaml").write_text("not parsed", encoding="utf-8")
    (prior / "notes.txt").write_text("ignored", encoding="utf-8")

    plan = pgp.plan_generation(
        eval_type="system", name="checkout_risk", root=root, run_date="2026-01-02"
    )

    assert plan["requires_confirmation"] is True
    assert plan["prior_generation_directories"] == [
        {"path": str(prior), "kind": "directory", "yaml_file_count": 1}
    ]
    proposed = Path(plan["proposed_directory"])
    assert proposed == root / "checkout_risk_2026-01-02"
    assert not proposed.exists()


def test_matching_directory_without_yaml_is_not_a_prior_generation(tmp_path):
    root = tmp_path / "examples"
    empty_match = root / "checkout_risk_2026-01-01"
    empty_match.mkdir(parents=True)
    (empty_match / "README.md").write_text("not yaml", encoding="utf-8")

    plan = pgp.plan_generation(
        eval_type="harm", name="checkout_risk", root=root, run_date="2026-01-02"
    )

    assert plan["prior_generation_directories"] == []
    assert plan["requires_confirmation"] is False
    assert Path(plan["proposed_directory"]) == root / "checkout_risk_2026-01-02"


def test_same_day_collision_adds_ordinal_suffix(tmp_path):
    root = tmp_path / "examples"
    prior = root / "checkout_risk"
    dated = root / "checkout_risk_2026-01-02"
    ordinal = root / "checkout_risk_2026-01-02_2"
    for path in (prior, dated, ordinal):
        path.mkdir(parents=True)
    (prior / "eval_config.yml").write_text("not parsed", encoding="utf-8")

    plan = pgp.plan_generation(
        eval_type="harm", name="checkout_risk", root=root, run_date="2026-01-02"
    )

    assert Path(plan["proposed_directory"]) == root / "checkout_risk_2026-01-02_3"
    assert not Path(plan["proposed_directory"]).exists()


def test_cli_emits_json_for_system_eval(tmp_path, capsys):
    root = tmp_path / "examples"

    code = pgp.main(
        [
            "--eval-type",
            "system",
            "--name",
            "checkout_risk",
            "--root",
            str(root),
            "--date",
            "2026-01-02",
        ]
    )

    assert code == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["eval_type"] == "system"
    assert plan["requires_confirmation"] is False
    assert Path(plan["proposed_directory"]) == root / "checkout_risk"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
