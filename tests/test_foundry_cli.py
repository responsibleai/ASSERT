# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""CLI tests for ``assert-ai foundry push``.

Exercises argument parsing + result rendering. Real orchestration is
covered by :mod:`tests.test_foundry_pipeline`; these tests keep the
CLI layer thin and inject a stub ``push_run_dir`` via monkeypatch.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from click.testing import CliRunner

# The `[foundry]` extra (azure-ai-projects) backs every symbol reached
# from this module. Skip cleanly on a base install so CI's Tier 1 job
# (which does not install optional extras) can still collect the file.
pytest.importorskip("azure.ai.projects")

from assert_ai.cli import _parse_passing_when_true, cli
from assert_ai.integrations.foundry.evaluators import AssertEvaluatorSpec
from assert_ai.integrations.foundry.pipeline import (
    DatasetRef,
    DryRunResult,
    EvaluatorRef,
    PushError,
    PushResult,
)


# ── _parse_passing_when_true ────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("answer_quality=true", ("answer_quality", True)),
        ("answer_quality=false", ("answer_quality", False)),
        ("dim=1", ("dim", True)),
        ("dim=0", ("dim", False)),
        ("dim=yes", ("dim", True)),
        ("dim=no", ("dim", False)),
        ("dim=TRUE", ("dim", True)),  # case-insensitive
        ("  spaced  =  true  ", ("spaced", True)),  # trimmed
    ],
)
def test_parse_passing_when_true_accepts_common_forms(
    raw: str, expected: tuple[str, bool]
) -> None:
    assert _parse_passing_when_true(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "no-equals-sign",
        "=only-value",
        "name=",
        "name=maybe",
    ],
)
def test_parse_passing_when_true_rejects_bad_forms(raw: str) -> None:
    import click

    with pytest.raises(click.BadParameter):
        _parse_passing_when_true(raw)


# ── Group + push help ───────────────────────────────────────────────


def test_foundry_push_help_renders() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["foundry", "push", "--help"])

    assert result.exit_code == 0
    assert "Publish a completed ASSERT run" in result.output
    assert "--evaluator-mode" in result.output
    assert "code" in result.output and "prompt" in result.output and "both" in result.output
    assert "--dry-run" in result.output
    assert "--passing-when-true" in result.output
    assert "--dataset-name" in result.output


# ── Stub-injection helpers ──────────────────────────────────────────


@dataclass
class _StubDryRun:
    """Mirror of DryRunResult with the same __class__ hook the CLI uses."""

    eval_name: str
    run_name: str
    dataset_name: str
    dataset_version: str
    dataset_row_count: int
    evaluator_specs: tuple
    judge_deployment: str
    passing_when_true: dict


def _stub_dry_run(*_: Any, **__: Any) -> DryRunResult:
    return DryRunResult(
        eval_name="ASSERT: sample",
        run_name="ASSERT run: r1",
        dataset_name="assert-sample",
        dataset_version="deadbeef1234",
        dataset_row_count=42,
        evaluator_specs=(),  # not printed in detail; count is 0
        evaluator_fingerprints={},
        judge_deployment="gpt-5.4-mini",
        passing_when_true={},
        prompt_variant_calls=0,
    )


def _stub_push_result(*_: Any, **__: Any) -> PushResult:
    return PushResult(
        eval_id="eval_stub",
        run_id="evalrun_stub",
        evaluator_refs=(
            EvaluatorRef(
                dimension_id="policy_violation",
                variant="code",
                evaluator_name="assert-policy_violation",
                evaluator_version="1",
            ),
            EvaluatorRef(
                dimension_id="policy_violation",
                variant="prompt",
                evaluator_name="assert-policy_violation-rescore",
                evaluator_version="1",
            ),
        ),
        dataset_ref=DatasetRef(
            name="assert-sample",
            version="deadbeef1234",
            asset_id="azureai://.../data/assert-sample/versions/deadbeef1234",
        ),
        reused_evaluators=("assert-policy_violation",),
        reused_dataset=True,
        reused_eval=False,
    )


def _install_stub(monkeypatch: Any, stub: Any) -> None:
    """Replace ``push_run_dir`` at the lazy-load boundary the CLI uses."""
    import assert_ai.integrations.foundry as foundry

    monkeypatch.setattr(foundry, "push_run_dir", stub, raising=False)
    # Also patch the lazy-load map so the CLI's _load_foundry_symbol picks up
    # the stub (the CLI reads the attribute off the package, so patching the
    # attribute is enough on Python 3.9+; belt-and-suspenders anyway).
    monkeypatch.setitem(
        foundry._LAZY_EXPORTS,
        "push_run_dir",
        "pipeline",
    )


# ── Dry-run path ────────────────────────────────────────────────────


def test_push_dry_run_prints_summary(monkeypatch: Any, tmp_path: Any) -> None:
    _install_stub(monkeypatch, _stub_dry_run)

    run_dir = tmp_path / "run"
    run_dir.mkdir()

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "foundry",
            "push",
            str(run_dir),
            "--project",
            "acct/proj",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Dry-run" in result.output
    assert "ASSERT: sample" in result.output
    assert "ASSERT run: r1" in result.output
    assert "assert-sample" in result.output
    assert "deadbeef1234" in result.output
    # Rich table renders label + value on the same line; check both are present.
    assert "Dataset rows" in result.output
    assert "42" in result.output
    assert "gpt-5.4-mini" in result.output


# ── Real-push path ──────────────────────────────────────────────────


def test_push_prints_ids_and_reuse_markers(monkeypatch: Any, tmp_path: Any) -> None:
    _install_stub(monkeypatch, _stub_push_result)

    run_dir = tmp_path / "run"
    run_dir.mkdir()

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "foundry",
            "push",
            str(run_dir),
            "--project",
            "acct/proj",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "eval_stub" in result.output
    assert "evalrun_stub" in result.output
    # Dataset reused flag surfaces.
    assert "Dataset" in result.output
    assert "(reused)" in result.output
    # Per-evaluator rows list both variants with the reuse marker on one.
    assert "assert-policy_violation" in result.output
    assert "assert-policy_violation-rescore" in result.output
    assert "code" in result.output
    assert "prompt" in result.output
    assert "reused" in result.output
    assert "new" in result.output


# ── JSON output ────────────────────────────────────────────────────


def test_push_dry_run_json_output(monkeypatch: Any, tmp_path: Any) -> None:
    import json as _json

    _install_stub(monkeypatch, _stub_dry_run)

    run_dir = tmp_path / "run"
    run_dir.mkdir()

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "foundry",
            "push",
            str(run_dir),
            "--project",
            "acct/proj",
            "--dry-run",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = _json.loads(result.output)
    assert payload["dry_run"] is True
    assert payload["eval_name"] == "ASSERT: sample"
    assert payload["run_name"] == "ASSERT run: r1"
    assert payload["dataset_name"] == "assert-sample"
    assert payload["dataset_version"] == "deadbeef1234"
    assert payload["dataset_row_count"] == 42
    assert payload["judge_deployment"] == "gpt-5.4-mini"
    assert payload["passing_when_true"] == {}
    assert payload["evaluators"] == []


def test_push_result_json_output(monkeypatch: Any, tmp_path: Any) -> None:
    import json as _json

    _install_stub(monkeypatch, _stub_push_result)

    run_dir = tmp_path / "run"
    run_dir.mkdir()

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "foundry",
            "push",
            str(run_dir),
            "--project",
            "acct/proj",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = _json.loads(result.output)
    assert payload["dry_run"] is False
    assert payload["eval_id"] == "eval_stub"
    assert payload["run_id"] == "evalrun_stub"
    assert payload["reused_eval"] is False
    assert payload["dataset"]["name"] == "assert-sample"
    assert payload["dataset"]["version"] == "deadbeef1234"
    assert payload["dataset"]["reused"] is True
    assert payload["evaluators"] == [
        {
            "name": "assert-policy_violation",
            "version": "1",
            "variant": "code",
            "reused": True,
        },
        {
            "name": "assert-policy_violation-rescore",
            "version": "1",
            "variant": "prompt",
            "reused": False,
        },
    ]


# ── Error propagation ──────────────────────────────────────────────


def _stub_raises_push_error(*_: Any, **__: Any) -> None:
    raise PushError("test failure with clear reason")


def test_push_prints_push_error_and_exits_nonzero(
    monkeypatch: Any, tmp_path: Any
) -> None:
    _install_stub(monkeypatch, _stub_raises_push_error)

    run_dir = tmp_path / "run"
    run_dir.mkdir()

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "foundry",
            "push",
            str(run_dir),
            "--project",
            "acct/proj",
        ],
    )

    assert result.exit_code != 0
    assert "test failure with clear reason" in result.output


# ── Argument parsing wiring ─────────────────────────────────────────


def test_push_evaluator_mode_choice_is_validated() -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "foundry",
            "push",
            ".",
            "--project",
            "acct/proj",
            "--evaluator-mode",
            "bogus",
        ],
    )

    assert result.exit_code != 0
    assert "bogus" in result.output.lower() or "invalid" in result.output.lower()


def test_push_requires_project_for_real_push(tmp_path: Any) -> None:
    """A real (non-dry-run) push without --project is rejected with a clear message."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    runner = CliRunner()
    result = runner.invoke(cli, ["foundry", "push", str(run_dir)])

    assert result.exit_code != 0
    assert "--project" in result.output


def test_push_dry_run_works_without_project(monkeypatch: Any, tmp_path: Any) -> None:
    """--dry-run makes no network calls, so --project is optional in dry-run mode.

    Regression guard: the runbook advertises dry-run as the fastest
    way to catch config mistakes before touching Foundry, so it must
    work in a fresh checkout with no exported project id.
    """
    captured: dict[str, Any] = {}

    def _capture(*args: Any, **kwargs: Any) -> DryRunResult:
        captured.update(kwargs)
        return _stub_dry_run()

    _install_stub(monkeypatch, _capture)

    run_dir = tmp_path / "run"
    run_dir.mkdir()

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["foundry", "push", str(run_dir), "--dry-run"],
    )

    assert result.exit_code == 0, result.output
    assert "Dry-run" in result.output
    # The pipeline receives project=None; dry-run path ignores it.
    assert captured.get("project") is None
    assert captured.get("dry_run") is True


def test_push_passing_when_true_repeatable(monkeypatch: Any, tmp_path: Any) -> None:
    """Repeated --passing-when-true flags accumulate into a dict."""
    captured: dict[str, Any] = {}

    def _capture(*args: Any, **kwargs: Any) -> DryRunResult:
        captured.update(kwargs)
        return _stub_dry_run()

    _install_stub(monkeypatch, _capture)

    run_dir = tmp_path / "run"
    run_dir.mkdir()

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "foundry",
            "push",
            str(run_dir),
            "--project",
            "acct/proj",
            "--dry-run",
            "--passing-when-true",
            "answer_quality=true",
            "--passing-when-true",
            "helpfulness=true",
            "--passing-when-true",
            "refused=false",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["passing_when_true"] == {
        "answer_quality": True,
        "helpfulness": True,
        "refused": False,
    }
