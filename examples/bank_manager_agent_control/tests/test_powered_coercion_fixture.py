from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

import yaml

EXAMPLE = Path(__file__).resolve().parents[1]
FIXTURE = EXAMPLE / "fixtures" / "coercion_powered_120.jsonl"
LABELS = EXAMPLE / "fixtures" / "coercion_powered_120_labels.json"
RESULTS = EXAMPLE / "fixtures" / "coercion_powered_120_results.json"
EXPECTED_SHA256 = "1f314b96e5ea372787e9b0481990a33ce45c920f74f63eb9cf70238d773260d3"
CONFIGS = (
    EXAMPLE / "eval_coercion_authority.yaml",
    EXAMPLE / "eval_coercion_arm2_hardened.yaml",
    EXAMPLE / "eval_coercion_arm3_acs.yaml",
)


def test_powered_fixture_hash_and_balance() -> None:
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == EXPECTED_SHA256
    rows = [
        json.loads(line)
        for line in FIXTURE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    labels = json.loads(LABELS.read_text(encoding="utf-8"))
    assert len(rows) == len(labels) == 120
    assert Counter(label["final_label"] for label in labels) == {
        "coercive": 60,
        "legit_evidenced": 30,
        "legit_routine": 30,
    }
    assert {row["test_case_id"] for row in rows} == {
        label["test_case_id"] for label in labels
    }


def test_all_arms_use_the_same_frozen_suite() -> None:
    for path in CONFIGS:
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert config["suite"] == "bank-manager-coercion-powered-120"
        test_set = config["pipeline"]["test_set"]
        assert test_set["enabled"] is False
        assert test_set["prompt"]["sample_size"] == 120
        assert "scenario" not in test_set


def test_published_result_summary_matches_blog_claims() -> None:
    results = json.loads(RESULTS.read_text(encoding="utf-8"))
    assert results["design"]["n_total"] == 120
    assert results["design"]["n_coercive"] == 60
    assert results["design"]["n_legitimate"] == 60

    expected = {
        "baseline": ((5, 60), (16, 60)),
        "hardened_prompt": ((0, 60), (28, 60)),
        "classifier": ((0, 60), (16, 60)),
    }
    for arm, (bypass, permissible) in expected.items():
        actual = results["arms"][arm]
        assert (
            actual["coercion_bypass"]["k"],
            actual["coercion_bypass"]["n"],
        ) == bypass
        assert (
            actual["legitimate_overrefusal"]["k"],
            actual["legitimate_overrefusal"]["n"],
        ) == permissible

    comparison = results["comparisons"]["classifier_vs_hardened_overrefusal"]
    assert comparison["rate_difference_classifier_minus_hardened"] == -0.2
    assert comparison["mcnemar"]["two_sided_exact_p"] == 0.01690053939819336
    assert (
        results["arms"]["classifier"]["coercion_bypass"][
            "one_sided_exact_upper95"
        ]
        == 0.04870291331009749
    )


def test_behavior_one_uses_callable_otel_trace() -> None:
    path = EXAMPLE / "eval_tier_authorization.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    target = config["pipeline"]["inference"]["target"]
    assert target["callable"].endswith(":chat_baseline_tier_authz")
    assert target["trace"] == {"backend": "otel", "group_by": "session.id"}
    assert not (EXAMPLE / "eval_tier_authorization_traced.yaml").exists()
    assert not (EXAMPLE / "agent_tier_authz_adapter.py").exists()
