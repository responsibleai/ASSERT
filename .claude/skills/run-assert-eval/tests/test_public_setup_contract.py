from __future__ import annotations

import re
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[4]
SKILL_DIR = REPO / ".claude" / "skills" / "run-assert-eval"
sys.path.insert(0, str(SKILL_DIR))

import clarity_intake as ci  # noqa: E402

SKILL = REPO / ".claude" / "skills" / "run-assert-eval" / "SKILL.md"
BUG_BASH = REPO / ".claude" / "skills" / "run-assert-eval" / "BUG_BASH.md"
SETUP = REPO / ".claude" / "skills" / "run-assert-eval" / "SETUP-CHECKLIST.md"
PROMPT = REPO / ".github" / "prompts" / "run-assert-eval.prompt.md"
CURSOR = REPO / ".cursor" / "rules" / "assert.mdc"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_public_bug_bash_has_only_existing_public_lanes() -> None:
    text = read(BUG_BASH)
    lanes = re.findall(r"^### Lane \d+ — `([^`]+)`", text, flags=re.MULTILINE)

    assert len(lanes) == 7
    assert "career_health_assessment" not in lanes
    for lane in lanes:
        assert (REPO / "examples" / lane).is_dir(), lane
        assert (
            REPO / "examples" / lane / "Clarity Protocol" / "failures" / "failures.md"
        ).is_file(), lane


def test_every_public_lane_answer_key_produces_atomic_candidates_with_dimensions() -> None:
    text = read(BUG_BASH)
    lanes = re.findall(r"^### Lane \d+ — `([^`]+)`", text, flags=re.MULTILINE)

    for lane in lanes:
        failures = REPO / "examples" / lane / "Clarity Protocol" / "failures"
        candidates = ci.build_candidate_behaviors(failures)
        assert len(candidates) >= 2, lane
        for candidate in candidates[:2]:
            assert not candidate.multi_behavior, (lane, candidate.name)
            assert candidate.candidate_dimensions, (lane, candidate.name, candidate.warnings)
            assert not any("no variants" in warning for warning in candidate.warnings), (
                lane,
                candidate.name,
            )


def test_bug_bash_references_flat_atomic_eval_paths() -> None:
    text = read(BUG_BASH)

    assert "evals/*/eval_config.yaml" not in text
    assert "evals/*.yaml" in text
    assert "five-case prompt-only smoke" in text


def test_setup_uses_the_pinned_bootstrap_not_broken_clarity_commands() -> None:
    combined = "\n".join(read(path) for path in (SKILL, PROMPT, CURSOR, SETUP, BUG_BASH))

    assert "setup_clarity.py ." in combined
    assert "clarity embed ." not in combined
    assert "clarity doctor" not in combined
    assert "clarity-agent checkout" not in combined


def test_all_three_skill_surfaces_require_the_same_smoke_shape() -> None:
    for path in (SKILL, PROMPT, CURSOR):
        text = read(path)
        assert "--smoke" in text, path
        assert "five prompt cases" in text.lower() or "5 prompt cases" in text.lower(), path
        assert "no scenarios" in text.lower(), path
        assert "concurrency 5" in text.lower(), path


def test_all_three_skill_surfaces_pool_prompt_and_scenario_results() -> None:
    for path in (SKILL, PROMPT, CURSOR):
        text = read(path)
        assert "--json --summary-only" in text, path
        assert "prompt_metrics" in text, path
        assert "scenario_metrics" in text, path
        assert "flagged_count" in text, path
        assert "applicable_count" in text, path


def test_dev_maintainer_owns_first_run_release_readiness() -> None:
    text = read(REPO / ".github" / "agents" / "dev-maintainer.md")

    assert "First-run release readiness" in text
    assert "fresh worktree" in text
    assert "five-case" in text
    assert "15 minutes" in text
