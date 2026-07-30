"""Tests for inter-rater agreement and judge comparability.

A 2-1 split and a 3-0 consensus otherwise produce identical output, and a judge
swap moves measured rates on an unchanged target with nothing flagging it.
"""

from __future__ import annotations

import pytest

from assert_ai.analysis.stats import KAPPA_WARN_THRESHOLD, fleiss_kappa
from assert_ai.results import (
    compute_judge_agreement,
    compute_judge_fingerprint,
    warn_if_judge_changed,
)


class TestFleissKappa:
    def test_matches_published_worked_example(self):
        """Fleiss (1971): 10 subjects, 14 raters, 5 categories, kappa = 0.210."""
        counts = [
            [0, 0, 0, 0, 14],
            [0, 2, 6, 4, 2],
            [0, 0, 3, 5, 6],
            [0, 3, 9, 2, 0],
            [2, 2, 8, 1, 1],
            [7, 7, 0, 0, 0],
            [3, 2, 6, 3, 0],
            [2, 5, 3, 2, 2],
            [6, 5, 2, 1, 0],
            [0, 2, 2, 3, 7],
        ]
        ratings = [
            [f"c{cat}" for cat, n in enumerate(row) for _ in range(n)] for row in counts
        ]
        assert fleiss_kappa(ratings) == pytest.approx(0.2099, abs=5e-4)

    def test_unanimous_is_one(self):
        assert fleiss_kappa([["a", "a", "a"], ["b", "b", "b"]]) == 1.0

    def test_single_category_everywhere_is_one(self):
        """Degenerate 0/0 case: chance agreement is total and so is observed."""
        assert fleiss_kappa([["a", "a"], ["a", "a"]]) == 1.0

    def test_chance_level_is_near_zero(self):
        """Balanced disagreement carries no information beyond chance."""
        ratings = [["a", "b"], ["b", "a"], ["a", "b"], ["b", "a"]]
        assert fleiss_kappa(ratings) == pytest.approx(-1.0, abs=1e-9)

    def test_perfect_split_below_threshold(self):
        ratings = [["a", "b"] for _ in range(10)]
        kappa = fleiss_kappa(ratings)
        assert kappa is not None and kappa < KAPPA_WARN_THRESHOLD

    def test_single_rater_is_undefined(self):
        assert fleiss_kappa([["a"], ["b"]]) is None

    def test_ragged_rater_counts_are_undefined(self):
        assert fleiss_kappa([["a", "a"], ["b"]]) is None

    def test_empty_is_undefined(self):
        assert fleiss_kappa([]) is None

    def test_none_is_a_real_category_not_a_gap(self):
        """A judge marking not-applicable took a position; it is not missing data."""
        assert fleiss_kappa([[None, None], [True, True]]) == 1.0
        assert fleiss_kappa([[None, True], [True, None]]) == pytest.approx(-1.0, abs=1e-9)

    def test_bool_and_string_votes_do_not_collide(self):
        assert fleiss_kappa([[True, "True"], ["True", True]]) == pytest.approx(
            -1.0, abs=1e-9
        )


def _row(votes: dict[str, list], **extra):
    row = {
        "judge_model": "azure/gpt-x",
        "multi_judge": {"n": len(next(iter(votes.values()))), "votes": votes},
        "verdict": {
            "dimensions": {"policy_violation": False},
            "node_judgments": [],
        },
        "score_keys": ["policy_violation"],
    }
    row.update(extra)
    return row


class TestComputeJudgeAgreement:
    def test_single_judge_run_has_no_agreement(self):
        rows = [{"judge_model": "m", "multi_judge": {"n": 1, "votes": {"d": [True]}}}]
        assert compute_judge_agreement(rows) is None

    def test_rows_without_multi_judge_return_none(self):
        assert compute_judge_agreement([{"judge_model": "m"}]) is None

    def test_consensus_scores_higher_than_a_split(self):
        consensus = [_row({"policy_violation": [True] * 3}) for _ in range(5)]
        consensus += [_row({"policy_violation": [False] * 3}) for _ in range(5)]
        split = [_row({"policy_violation": [True, True, False]}) for _ in range(5)]
        split += [_row({"policy_violation": [False, False, True]}) for _ in range(5)]

        k_consensus = compute_judge_agreement(consensus)["by_dimension"][
            "policy_violation"
        ]["kappa"]
        k_split = compute_judge_agreement(split)["by_dimension"]["policy_violation"][
            "kappa"
        ]
        assert k_consensus > k_split

    def test_low_agreement_is_flagged_and_warned(self, caplog):
        rows = [_row({"policy_violation": [True, False]}) for _ in range(8)]
        with caplog.at_level("WARNING"):
            result = compute_judge_agreement(rows)
        assert result["by_dimension"]["policy_violation"]["low_agreement"] is True
        assert "Low inter-rater agreement" in caplog.text

    def test_reports_items_and_raters(self):
        rows = [_row({"policy_violation": [True] * 3}) for _ in range(4)]
        entry = compute_judge_agreement(rows)["by_dimension"]["policy_violation"]
        assert entry["items"] == 4
        assert entry["raters"] == 3


class TestJudgeFingerprint:
    def test_same_configuration_matches(self):
        a = compute_judge_fingerprint([_row({"policy_violation": [True, True]})])
        b = compute_judge_fingerprint([_row({"policy_violation": [True, True]})])
        assert a["fingerprint"] == b["fingerprint"]

    def test_model_change_changes_fingerprint(self):
        a = compute_judge_fingerprint([_row({"policy_violation": [True, True]})])
        b = compute_judge_fingerprint(
            [_row({"policy_violation": [True, True]}, judge_model="azure/gpt-y")]
        )
        assert a["fingerprint"] != b["fingerprint"]

    def test_prompt_change_changes_fingerprint(self):
        base = _row({"policy_violation": [True, True]})
        changed = _row({"policy_violation": [True, True]}, judge_prompt_sha="deadbeef")
        assert (
            compute_judge_fingerprint([base])["fingerprint"]
            != compute_judge_fingerprint([changed])["fingerprint"]
        )

    def test_warns_only_when_fingerprint_differs(self, caplog):
        a = compute_judge_fingerprint([_row({"policy_violation": [True, True]})])
        b = compute_judge_fingerprint(
            [_row({"policy_violation": [True, True]}, judge_model="azure/gpt-y")]
        )
        with caplog.at_level("WARNING"):
            assert warn_if_judge_changed(a, a) is False
            assert "not directly comparable" not in caplog.text
            assert warn_if_judge_changed(b, a) is True
        assert "not directly comparable" in caplog.text

    def test_missing_sides_do_not_warn(self):
        a = compute_judge_fingerprint([_row({"policy_violation": [True, True]})])
        assert warn_if_judge_changed(a, None) is False
        assert warn_if_judge_changed(None, a) is False
