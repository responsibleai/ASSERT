# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "sandbox_action_mediation"
RUNNER = EXAMPLE / "run_scenario.py"
STOCK_RUNNER = EXAMPLE / "run_stock_scenario.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    return subprocess.run(
        [sys.executable, str(RUNNER), *args],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def _copy_example(tmp_path: Path) -> Path:
    copied = tmp_path / "scenario"
    shutil.copytree(EXAMPLE, copied)
    return copied


def _set_send_mode(directory: Path, mode: str) -> None:
    path = directory / "policy.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    rule = next(rule for rule in data["interactions"] if rule["match"] == "send_message")
    rule["mode"] = mode
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def test_bug_bash_scenario_starts_in_mock_mode_and_writes_no_artifacts(tmp_path):
    copied = _copy_example(tmp_path)
    before = sorted(path.relative_to(copied) for path in copied.rglob("*") if path.is_file())

    result = _run(
        "--setup", str(copied / "assert-setup.yaml"),
        "--scenario", str(copied / "scenario.yaml"),
        "--expect", "mock",
        "--json",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["pass"] is True
    assert payload["outcome"]["mode"] == "mock"
    assert payload["outcome"]["real_executed"] is False
    assert payload["evidence"]["tool_name"] == "send_message"
    assert payload["evidence"]["matched_args"] == ["recipient"]
    after = sorted(path.relative_to(copied) for path in copied.rglob("*") if path.is_file())
    assert after == before, "the fast bug-bash path must not leave generated artifacts"


def test_bug_bash_participant_can_change_policy_to_block(tmp_path):
    copied = _copy_example(tmp_path)
    _set_send_mode(copied, "block")

    result = _run(
        "--setup", str(copied / "assert-setup.yaml"),
        "--scenario", str(copied / "scenario.yaml"),
        "--expect", "block",
        "--json",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["outcome"]["mode"] == "block"
    assert payload["outcome"]["real_executed"] is False
    assert payload["outcome"]["agent_received"]["status"] == "blocked"
    assert payload["evidence"]["mock_rule"] is None, "block must not consult mock fidelity"


def test_bug_bash_scenario_fails_loudly_if_policy_allows_real_send(tmp_path):
    copied = _copy_example(tmp_path)
    _set_send_mode(copied, "pass")

    result = _run(
        "--setup", str(copied / "assert-setup.yaml"),
        "--scenario", str(copied / "scenario.yaml"),
    )

    assert result.returncode != 0
    assert "CONTAINMENT FAILURE" in result.stderr
    assert "real send_message implementation executed" in result.stderr


def test_bug_bash_guide_routes_people_across_focused_assignments():
    guide = (EXAMPLE / "BUG_BASH.md").read_text(encoding="utf-8")
    for assignment in "ABCD":
        assert f"Assignment {assignment}" in guide
    assert "Assignment E" not in guide
    assert "run_stock_scenario.py --check-baseline" in guide
    lowered = guide.lower()
    assert "configure a per-use-case mock" in lowered
    assert "test the policy boundary" in lowered
    assert "state or failure handling" in lowered
    assert "stretch ideas" in lowered
    assert STOCK_RUNNER.exists()


def test_bug_bash_guide_explains_assignment_b_evidence_and_windows_setup():
    guide = (EXAMPLE / "BUG_BASH.md").read_text(encoding="utf-8")
    assignment_b = guide.split("## Assignment B", 1)[1].split("## Assignment C", 1)[0]

    assert "docker info" in guide
    assert "Start Docker Desktop" in guide
    assert (
        "docker build -f examples/sandbox_action_mediation/stock_agent/Dockerfile "
        "-t assert-sandbox-stock-agent:local ."
    ) in guide

    assert "ASSIGNMENT_B_CUSTOM_STATUS" in assignment_b
    assert "ASSIGNMENT_B_CUSTOM_MESSAGE" in assignment_b
    assert "dry-run preview" in assignment_b
    assert "send_message: mode=mock real_executed=false matched=send_message" in assignment_b
    assert '"message": "ASSIGNMENT_B_CUSTOM_MESSAGE"' in assignment_b
    assert '"status": "ASSIGNMENT_B_CUSTOM_STATUS"' in assignment_b
    assert "network_egress" in assignment_b
    assert "separate containment check" in assignment_b
    assert "--check-baseline" in assignment_b
    assert "expects the original" in assignment_b
    assert "assets/assignment-b-expected-output.png" in assignment_b
    assert (EXAMPLE / "assets" / "assignment-b-expected-output.png").exists()
