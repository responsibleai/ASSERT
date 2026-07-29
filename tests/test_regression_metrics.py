"""Unit tests for permissibility-split regression metrics."""

from __future__ import annotations

import unittest
from typing import Any

from scripts import regression_metrics as rm


POLICY = {
    "behavior_categories": [
        {"name": "perm-a", "permissible": True},
        {"name": "perm-b", "permissible": True},
        {"name": "not-perm-a", "permissible": False},
    ]
}


def _node(
    *,
    node_index: int | None,
    node_name: str,
    relevant: bool,
    violated: bool | None,
) -> dict[str, Any]:
    node: dict[str, Any] = {
        "node_name": node_name,
        "relevant": relevant,
        "violated": violated,
    }
    if node_index is not None:
        node["node_index"] = node_index
    return node


def _score(
    test_case_id: str,
    *,
    behavior: str = "not-perm-a",
    nodes: list[Any] | None = None,
    judge_status: str = "ok",
) -> dict[str, Any]:
    if judge_status != "ok":
        return {
            "test_case_id": test_case_id,
            "dimensions": {"behavior": behavior},
            "judge_status": judge_status,
            "verdict": None,
        }
    return {
        "test_case_id": test_case_id,
        "dimensions": {"behavior": behavior},
        "judge_status": "ok",
        "verdict": {
            "dimensions": {
                "policy_violation": False,
                "overrefusal": False,
            },
            "node_judgments": nodes or [],
        },
    }


class PolicyViolationByPermissibilityTest(unittest.TestCase):
    def test_uses_one_any_violation_vote_per_conversation(self) -> None:
        rows = [
            _score(
                "s1",
                nodes=[
                    _node(
                        node_index=0,
                        node_name="perm-a",
                        relevant=True,
                        violated=True,
                    ),
                    _node(
                        node_index=1,
                        node_name="perm-b",
                        relevant=True,
                        violated=False,
                    ),
                    _node(
                        node_index=2,
                        node_name="not-perm-a",
                        relevant=False,
                        violated=None,
                    ),
                ],
            ),
            _score(
                "s2",
                behavior="perm-a",
                nodes=[
                    _node(
                        node_index=0,
                        node_name="perm-a",
                        relevant=True,
                        violated=False,
                    ),
                    _node(
                        node_index=2,
                        node_name="not-perm-a",
                        relevant=True,
                        violated=True,
                    ),
                ],
            ),
            _score(
                "s3",
                behavior="perm-a",
                nodes=[
                    _node(
                        node_index=2,
                        node_name="not-perm-a",
                        relevant=True,
                        violated=False,
                    ),
                ],
            ),
        ]

        metrics = rm.compute_all(rows, POLICY)
        permissible = metrics[rm.PERMISSIBLE_POLICY_VIOLATION_RATE]
        not_permissible = metrics[rm.NOT_PERMISSIBLE_POLICY_VIOLATION_RATE]

        self.assertEqual(permissible.per_test_case, {"s1": 1, "s2": 0})
        self.assertEqual(permissible.value, 0.5)
        self.assertEqual(permissible.detail["applicable_count"], 2)
        self.assertEqual(permissible.detail["not_applicable_count"], 1)
        self.assertEqual(permissible.detail["flagged_count"], 1)
        self.assertEqual(permissible.detail["clear_count"], 1)

        self.assertEqual(not_permissible.per_test_case, {"s2": 1, "s3": 0})
        self.assertEqual(not_permissible.value, 0.5)
        self.assertEqual(not_permissible.detail["applicable_count"], 2)
        self.assertEqual(not_permissible.detail["not_applicable_count"], 1)

    def test_uses_relevant_nodes_not_originating_behavior(self) -> None:
        rows = [
            _score(
                "s1",
                behavior="not-perm-a",
                nodes=[
                    _node(
                        node_index=0,
                        node_name="perm-a",
                        relevant=True,
                        violated=True,
                    ),
                    _node(
                        node_index=2,
                        node_name="not-perm-a",
                        relevant=False,
                        violated=None,
                    ),
                ],
            )
        ]

        metrics = rm.compute_all(rows, POLICY)

        self.assertEqual(
            metrics[rm.PERMISSIBLE_POLICY_VIOLATION_RATE].per_test_case,
            {"s1": 1},
        )
        self.assertEqual(
            metrics[rm.NOT_PERMISSIBLE_POLICY_VIOLATION_RATE].per_test_case,
            {},
        )

    def test_prefers_valid_node_index_and_falls_back_to_name(self) -> None:
        rows = [
            _score(
                "by-index",
                nodes=[
                    _node(
                        node_index=0,
                        node_name="stale-name",
                        relevant=True,
                        violated=True,
                    ),
                ],
            ),
            _score(
                "by-name",
                nodes=[
                    _node(
                        node_index=999,
                        node_name="not-perm-a",
                        relevant=True,
                        violated=True,
                    ),
                ],
            ),
        ]

        metrics = rm.compute_all(rows, POLICY)

        self.assertEqual(
            metrics[rm.PERMISSIBLE_POLICY_VIOLATION_RATE].per_test_case,
            {"by-index": 1},
        )
        self.assertEqual(
            metrics[rm.NOT_PERMISSIBLE_POLICY_VIOLATION_RATE].per_test_case,
            {"by-name": 1},
        )

    def test_ignores_failed_unknown_irrelevant_and_malformed_judgments(self) -> None:
        rows = [
            _score(
                "scored",
                nodes=[
                    None,
                    "malformed",
                    _node(
                        node_index=0,
                        node_name="perm-a",
                        relevant=False,
                        violated=True,
                    ),
                    _node(
                        node_index=999,
                        node_name="unknown",
                        relevant=True,
                        violated=True,
                    ),
                    _node(
                        node_index=2,
                        node_name="not-perm-a",
                        relevant=True,
                        violated=None,
                    ),
                ],
            ),
            _score("failed", judge_status="judge_failed"),
        ]

        metrics = rm.compute_all(rows, POLICY)

        for metric in metrics.values():
            self.assertEqual(metric.per_test_case, {})
            self.assertEqual(metric.detail["not_applicable_count"], 1)

    def test_empty_policy_returns_empty_metric_samples(self) -> None:
        metrics = rm.compute_all([_score("s1")], {})

        self.assertEqual(set(metrics), set(rm.CANONICAL_METRICS))
        for metric in metrics.values():
            self.assertEqual(metric.value, 0.0)
            self.assertEqual(metric.per_test_case, {})
            self.assertEqual(metric.detail["reason"], "empty_policy")

    def test_metric_set_and_directions_are_exact(self) -> None:
        self.assertEqual(
            rm.CANONICAL_METRICS,
            (
                "permissible_policy_violation_rate",
                "not_permissible_policy_violation_rate",
            ),
        )
        self.assertEqual(rm.PER_TEST_CASE_BINARY, rm.CANONICAL_METRICS)
        self.assertEqual(
            set(rm.DIRECTIONS.values()),
            {"lower_is_better"},
        )

    def test_normalizes_legacy_permissibility_values(self) -> None:
        policy = {
            "behavior_categories": [
                {"name": "perm", "permissible": "true"},
                {"name": "not-perm", "permissible": "false"},
            ]
        }
        rows = [
            _score(
                "s1",
                nodes=[
                    _node(
                        node_index=0,
                        node_name="perm",
                        relevant=True,
                        violated=True,
                    ),
                    _node(
                        node_index=1,
                        node_name="not-perm",
                        relevant=True,
                        violated=True,
                    ),
                ],
            )
        ]

        metrics = rm.compute_all(rows, policy)

        self.assertEqual(
            metrics[rm.PERMISSIBLE_POLICY_VIOLATION_RATE].per_test_case,
            {"s1": 1},
        )
        self.assertEqual(
            metrics[rm.NOT_PERMISSIBLE_POLICY_VIOLATION_RATE].per_test_case,
            {"s1": 1},
        )


if __name__ == "__main__":
    unittest.main()
