"""Tests for validate_dimension_review: dimension-review ledger contracts.

Every test builds its own ledger and config paths under ``tmp_path``.

Run standalone:
    python -m pytest .claude/skills/run-assert-eval/tests/test_validate_dimension_review.py
"""

from __future__ import annotations

from copy import deepcopy
import sys
from pathlib import Path

import pytest
import yaml

# Make the skill dir importable without installing anything.
SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

import validate_dimension_review as vdr  # noqa: E402


def _candidate(prefix: str, number: int, namespace: str) -> dict[str, object]:
    return {
        "id": f"{prefix}{number}",
        "name": f"{namespace} candidate {number}",
        "disposition": "keep",
        "citation_tags": ["[1]", "[2]"],
    }


def _canonical(
    *,
    item_id: str,
    name: str,
    source_items: list[str],
    citation_tags: list[str],
) -> dict[str, object]:
    return {
        "id": item_id,
        "name": name,
        "purpose": f"Purpose for {name}.",
        "levels_or_mode": "categorical",
        "observability": "Observable in model output.",
        "executable": True,
        "aliases": [],
        "source_items": source_items,
        "source_passes": [1, 2],
        "citation_tags": citation_tags,
        "rationale": f"Retained {name} from both generation passes.",
        "intent_alignment": None,
    }


def _valid_ledger(*, approved: bool = False) -> dict[str, object]:
    status = "approved" if approved else "pending_review"
    approval_status = "approved" if approved else "pending"
    return {
        "schema_version": 1,
        "harm_name": "Checkout risk",
        "n": 2,
        "active_cycle": "cycle-1",
        "evaluation_intent": {
            "decision": None,
            "purposes": [],
            "population": None,
        },
        "references": {
            "[1]": {
                "title": "Reference one",
                "url": "https://example.test/one",
                "accessed": "2026-01-01",
            },
            "[2]": {
                "title": "Reference two",
                "url": "https://example.test/two",
                "accessed": "2026-01-01",
            },
        },
        "cycles": [
            {
                "id": "cycle-1",
                "criteria_version": "criteria-v1",
                "criteria": ["Generate researched, executable dimensions."],
                "status": status,
                "passes": [
                    {
                        "number": 1,
                        "complete": True,
                        "intent_fields_applied": [],
                        "search_branches": ["primary"],
                        "breadth_audit_complete": True,
                        "no_new_dimension_passes": 2,
                        "candidates": {
                            "behavior_categories": [
                                _candidate("b", 1, "behavior category")
                            ],
                            "test_dimensions": [_candidate("t", 1, "test dimension")],
                            "judge_dimensions": [_candidate("j", 1, "judge dimension")],
                        },
                    },
                    {
                        "number": 2,
                        "complete": True,
                        "intent_fields_applied": [],
                        "search_branches": ["primary"],
                        "breadth_audit_complete": True,
                        "no_new_dimension_passes": 2,
                        "candidates": {
                            "behavior_categories": [
                                _candidate("b", 2, "behavior category")
                            ],
                            "test_dimensions": [_candidate("t", 2, "test dimension")],
                            "judge_dimensions": [_candidate("j", 2, "judge dimension")],
                        },
                    },
                ],
                "deduplication": {
                    "completed": True,
                    "duplicate_audit_complete": True,
                    "namespaces": {
                        "behavior_categories": [
                            _canonical(
                                item_id="bc-1",
                                name="checkout_manipulation",
                                source_items=["b1", "b2"],
                                citation_tags=["[1]"],
                            )
                        ],
                        "test_dimensions": [
                            _canonical(
                                item_id="td-1",
                                name="checkout_context",
                                source_items=["t1", "t2"],
                                citation_tags=["[1]", "[2]"],
                            )
                        ],
                        "judge_dimensions": [
                            _canonical(
                                item_id="jd-1",
                                name="custom_checkout_safety_gap",
                                source_items=["j1", "j2"],
                                citation_tags=["[1]", "[2]"],
                            )
                        ],
                    },
                    "rejections": [],
                },
            }
        ],
        "approval": {
            "status": approval_status,
            "cycle_id": "cycle-1",
            "criteria_version": "criteria-v1",
            "relevance": "approved" if approved else "pending",
            "edits": "No edits requested." if approved else "Pending review.",
            "response": "Approved." if approved else "Pending review.",
            "approved_by": "user" if approved else "pending",
            "approved_at": "2026-01-01T00:00:00Z",
        },
    }


def _write_review(path: Path, data: dict[str, object], *, body: str | None = None) -> None:
    frontmatter = yaml.safe_dump(data, sort_keys=False)
    rendered = vdr.render_review_body(data) if body is None else body
    path.write_text(f"---\n{frontmatter}---\n{rendered}", encoding="utf-8")


def _review_path(tmp_path: Path, data: dict[str, object]) -> Path:
    path = tmp_path / "dimension-review.md"
    _write_review(path, data)
    return path


# --- validation -------------------------------------------------------------


def test_valid_ledger_passes_validate(tmp_path, capsys):
    review = _review_path(tmp_path, _valid_ledger())

    code = vdr.main(["validate", "--review", str(review)])

    assert code == 0
    assert "Validated" in capsys.readouterr().out


@pytest.mark.parametrize("name", ["policy_violation", "overrefusal"])
def test_judge_dimension_canonical_names_must_not_shadow_built_ins(name):
    data = _valid_ledger()
    data["cycles"][0]["deduplication"]["namespaces"]["judge_dimensions"][0]["name"] = name

    with pytest.raises(vdr.ReviewValidationError) as exc:
        vdr.validate_review(data)

    assert name in str(exc.value)


def test_builtin_judge_dimension_constant_matches_runtime_contract():
    assert vdr.BUILT_IN_JUDGE_DIMENSIONS == {"policy_violation", "overrefusal"}


def test_builtin_names_are_allowed_outside_judge_dimension_namespace():
    data = _valid_ledger()
    namespaces = data["cycles"][0]["deduplication"]["namespaces"]
    namespaces["behavior_categories"][0]["name"] = "policy_violation"
    namespaces["test_dimensions"][0]["name"] = "overrefusal"

    vdr.validate_review(data)


@pytest.mark.parametrize("pass_count", [1, 3])
def test_active_cycle_must_have_exactly_n_passes(pass_count):
    data = _valid_ledger()
    passes = data["cycles"][0]["passes"]
    if pass_count == 1:
        del passes[1]
    else:
        extra = deepcopy(passes[1])
        extra["number"] = 3
        passes.append(extra)

    with pytest.raises(vdr.ReviewValidationError, match="exactly n=2 passes"):
        vdr.validate_review(data)


def test_unresolved_citation_tag_fails_validation():
    data = _valid_ledger()
    candidate = data["cycles"][0]["passes"][0]["candidates"]["behavior_categories"][0]
    candidate["citation_tags"] = ["[3]"]

    with pytest.raises(vdr.ReviewValidationError, match=r"undefined citation \[3\]"):
        vdr.validate_review(data)


# --- pre-write / post-write -------------------------------------------------


def test_pre_write_requires_approved_review(tmp_path):
    review = _review_path(tmp_path, _valid_ledger(approved=False))

    with pytest.raises(vdr.ReviewValidationError, match="approval is required"):
        vdr.pre_write(review, tmp_path / "config.yaml", tmp_path / "stamp.json")


def test_pre_write_fails_if_config_path_already_exists_without_reading_it(tmp_path):
    review = _review_path(tmp_path, _valid_ledger(approved=True))
    config = tmp_path / "config.yaml"
    config.write_text("this is not yaml: [", encoding="utf-8")

    with pytest.raises(vdr.ReviewValidationError, match="already exists"):
        vdr.pre_write(review, config, tmp_path / "stamp.json")


def test_post_write_fails_if_review_changed_after_approval(tmp_path):
    data = _valid_ledger(approved=True)
    review = _review_path(tmp_path, data)
    config = tmp_path / "config.yaml"
    stamp = tmp_path / "stamp.json"
    vdr.pre_write(review, config, stamp)
    config.write_text("behavior:\n  name: checkout_risk\n", encoding="utf-8")
    changed = deepcopy(data)
    changed["approval"]["response"] = "Approved after one wording change."
    _write_review(review, changed)

    with pytest.raises(vdr.ReviewValidationError, match="review changed after pre-write"):
        vdr.post_write(review, config, stamp)


def test_post_write_fails_if_config_was_not_created_after_pre_write(tmp_path):
    review = _review_path(tmp_path, _valid_ledger(approved=True))
    config = tmp_path / "config.yaml"
    stamp = tmp_path / "stamp.json"
    vdr.pre_write(review, config, stamp)

    with pytest.raises(vdr.ReviewValidationError, match="config was not written"):
        vdr.post_write(review, config, stamp)


def test_pre_write_create_config_then_post_write_succeeds(tmp_path):
    review = _review_path(tmp_path, _valid_ledger(approved=True))
    config = tmp_path / "config.yaml"
    stamp = tmp_path / "stamp.json"

    vdr.pre_write(review, config, stamp)
    config.write_text("behavior:\n  name: checkout_risk\n", encoding="utf-8")
    vdr.post_write(review, config, stamp)

    stamp_data = yaml.safe_load(stamp.read_text(encoding="utf-8"))
    assert "config_after_sha256" in stamp_data
    assert "post_write_verified_at" in stamp_data


# --- render -----------------------------------------------------------------


def test_render_regenerates_markdown_body_from_frontmatter(tmp_path):
    data = _valid_ledger()
    review = tmp_path / "dimension-review.md"
    _write_review(review, data, body="stale body")

    code = vdr.main(["render", "--review", str(review)])

    assert code == 0
    _, _, body = vdr._split_review(review)
    assert body == vdr.render_review_body(data)


# --- anti-shadowing gate on the written config -------------------------------


@pytest.mark.parametrize("name", sorted(vdr.BUILT_IN_JUDGE_DIMENSIONS))
def test_written_config_may_not_shadow_built_in_judge_dimension(tmp_path, name):
    """The ledger gate guards the review; this guards the artifact the judge reads."""
    config = {"pipeline": {"judge": {"dimensions": {name: {"rubric": "mine"}}}}}

    with pytest.raises(vdr.ReviewValidationError) as exc:
        vdr._reject_shadowing_judge_dimensions(config, tmp_path / "eval_config.yaml")

    assert name in str(exc.value)


@pytest.mark.parametrize("name", sorted(vdr.BUILT_IN_JUDGE_DIMENSIONS))
def test_written_config_shadowing_is_caught_in_list_form(tmp_path, name):
    config = {"pipeline": {"judge": {"dimensions": [{"name": name, "rubric": "mine"}]}}}

    with pytest.raises(vdr.ReviewValidationError):
        vdr._reject_shadowing_judge_dimensions(config, tmp_path / "eval_config.yaml")


def test_written_config_allows_researched_judge_dimensions(tmp_path):
    config = {
        "pipeline": {
            "judge": {"dimensions": {"harm_actionability": {"rubric": "researched"}}}
        }
    }

    vdr._reject_shadowing_judge_dimensions(config, tmp_path / "eval_config.yaml")


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"pipeline": None},
        {"pipeline": {}},
        {"pipeline": {"judge": None}},
        {"pipeline": {"judge": {}}},
        {"pipeline": {"judge": {"dimensions": None}}},
        {"pipeline": {"judge": {"dimensions": []}}},
    ],
)
def test_written_config_gate_tolerates_missing_sections(tmp_path, config):
    vdr._reject_shadowing_judge_dimensions(config, tmp_path / "eval_config.yaml")


def test_post_write_rejects_shadowing_config(tmp_path):
    """End-to-end: the exploit that previously passed both gates is now blocked."""
    review = _review_path(tmp_path, _valid_ledger(approved=True))
    config = tmp_path / "config.yaml"
    stamp = tmp_path / "stamp.json"
    vdr.pre_write(review, config, stamp)
    config.write_text(
        yaml.safe_dump(
            {"pipeline": {"judge": {"dimensions": {"policy_violation": {"rubric": "x"}}}}}
        ),
        encoding="utf-8",
    )

    with pytest.raises(vdr.ReviewValidationError) as exc:
        vdr.post_write(review, config, stamp)

    assert "policy_violation" in str(exc.value)


@pytest.mark.parametrize(
    "preset", ["safety-core", ["safety-core"], ["safety-extended", "safety-core"]]
)
def test_written_config_may_not_select_a_shadowing_judge_preset(tmp_path, preset):
    """Presets expand into the same merged dimension list, so they shadow too."""
    config = {"pipeline": {"judge": {"preset": preset}}}

    with pytest.raises(vdr.ReviewValidationError) as exc:
        vdr._reject_shadowing_judge_dimensions(config, tmp_path / "eval_config.yaml")

    message = str(exc.value)
    assert "safety-core" in message
    assert "policy_violation" in message


def test_written_config_allows_a_purely_additive_judge_preset(tmp_path):
    config = {"pipeline": {"judge": {"preset": "safety-extended"}}}

    vdr._reject_shadowing_judge_dimensions(config, tmp_path / "eval_config.yaml")


def test_unresolvable_judge_preset_is_skipped_rather_than_failing(tmp_path):
    """This script must stay runnable outside the repo layout."""
    config = {"pipeline": {"judge": {"preset": "no-such-preset-anywhere"}}}

    vdr._reject_shadowing_judge_dimensions(config, tmp_path / "eval_config.yaml")


def test_safety_core_still_shadows_both_built_ins():
    """Guards the guidance change: if this preset ever stops shadowing, revisit it."""
    preset_file = vdr._find_judge_preset_file("safety-core")
    assert preset_file is not None, "safety-core preset should resolve from the repo"

    names = set(vdr._preset_dimension_names({"preset": "safety-core"})["safety-core"])
    assert names == set(vdr.BUILT_IN_JUDGE_DIMENSIONS)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))