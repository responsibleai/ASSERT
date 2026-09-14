"""Paired analysis for the reviewed 120-case coercion experiment.

The same 60 coercive and 60 legitimate prompts are executed in every arm.
Accordingly, comparisons use exact paired McNemar tests rather than independent
two-proportion tests.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
EXAMPLE = Path(__file__).resolve().parents[1]
SUITE = REPO / "artifacts" / "results" / "bank-manager-coercion-powered-120"
LABELS = EXAMPLE / "fixtures" / "coercion_powered_120_labels.json"

ARMS = {
    "baseline": "arm1-realistic-baseline",
    "hardened_prompt": "arm2-hardened-prompt",
    "classifier": "arm3-acs-calibrated-classifier",
}


def load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def wilson(k: int, n: int) -> tuple[float, float]:
    z = 1.959963984540054
    p = k / n
    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    spread = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / denominator
    return max(0.0, center - spread), min(1.0, center + spread)


def binomial_cdf(k: int, n: int, p: float) -> float:
    return sum(
        math.comb(n, i) * (p**i) * ((1 - p) ** (n - i))
        for i in range(k + 1)
    )


def exact_upper95(k: int, n: int) -> float:
    if k == n:
        return 1.0
    low = k / n
    high = 1.0
    for _ in range(100):
        mid = (low + high) / 2
        if binomial_cdf(k, n, mid) > 0.05:
            low = mid
        else:
            high = mid
    return high


def exact_mcnemar(left: list[bool], right: list[bool]) -> dict:
    left_only = sum(a and not b for a, b in zip(left, right))
    right_only = sum(b and not a for a, b in zip(left, right))
    discordant = left_only + right_only
    if discordant == 0:
        p_value = 1.0
    else:
        tail = sum(
            math.comb(discordant, index)
            for index in range(min(left_only, right_only) + 1)
        )
        p_value = min(1.0, 2 * tail / (2**discordant))
    return {
        "left_only": left_only,
        "right_only": right_only,
        "discordant": discordant,
        "two_sided_exact_p": p_value,
    }


def summarize(values: list[bool]) -> dict:
    k = sum(values)
    n = len(values)
    lower, upper = wilson(k, n)
    return {
        "k": k,
        "n": n,
        "rate": k / n,
        "ci95_wilson": [lower, upper],
    }


def main() -> int:
    labels = {
        row["test_case_id"]: row
        for row in json.loads(LABELS.read_text(encoding="utf-8"))
    }
    coercive_ids = [
        case_id
        for case_id, row in labels.items()
        if row["final_label"] == "coercive"
    ]
    legitimate_ids = [
        case_id
        for case_id, row in labels.items()
        if row["final_label"] != "coercive"
    ]

    arm_values: dict[str, dict[str, list[bool]]] = {}
    report: dict = {"arms": {}, "comparisons": {}}

    for arm, run in ARMS.items():
        score_path = SUITE / run / "scores.jsonl"
        if not score_path.exists():
            raise SystemExit(
                f"missing {score_path}; run all three powered configs first"
            )
        scores = {row["test_case_id"]: row for row in load_jsonl(score_path)}
        if set(scores) != set(labels):
            raise SystemExit(f"{arm}: score IDs do not match the reviewed fixture")
        if any(row.get("judge_status") != "ok" for row in scores.values()):
            raise SystemExit(f"{arm}: a judge failure is present")

        def dimension(case_id: str, name: str) -> bool:
            dimensions = (scores[case_id].get("verdict") or {}).get("dimensions") or {}
            return bool(dimensions.get(name))

        impermissible = [
            dimension(case_id, "coercion_bypass") for case_id in coercive_ids
        ]
        permissible = [
            dimension(case_id, "legitimate_escalation_overrefusal")
            for case_id in legitimate_ids
        ]
        arm_values[arm] = {
            "impermissible": impermissible,
            "permissible": permissible,
        }
        report["arms"][arm] = {
            "impermissible_coercion_bypass": summarize(impermissible),
            "permissible_legitimate_request_mishandled": summarize(permissible),
        }
        report["arms"][arm]["impermissible_coercion_bypass"][
            "one_sided_exact_upper95"
        ] = exact_upper95(sum(impermissible), len(impermissible))

    report["comparisons"]["classifier_vs_hardened_permissible"] = {
        "rate_difference_classifier_minus_hardened": (
            report["arms"]["classifier"][
                "permissible_legitimate_request_mishandled"
            ]["rate"]
            - report["arms"]["hardened_prompt"][
                "permissible_legitimate_request_mishandled"
            ]["rate"]
        ),
        "mcnemar": exact_mcnemar(
            arm_values["classifier"]["permissible"],
            arm_values["hardened_prompt"]["permissible"],
        ),
    }
    report["comparisons"]["classifier_vs_baseline_impermissible"] = {
        "rate_difference_classifier_minus_baseline": (
            report["arms"]["classifier"]["impermissible_coercion_bypass"]["rate"]
            - report["arms"]["baseline"]["impermissible_coercion_bypass"]["rate"]
        ),
        "mcnemar": exact_mcnemar(
            arm_values["classifier"]["impermissible"],
            arm_values["baseline"]["impermissible"],
        ),
    }

    print(
        f"{'arm':18s} {'impermissible: bypass':>26s} "
        f"{'permissible: legit mishandled':>32s}"
    )
    print("-" * 80)
    for arm in ARMS:
        imp = report["arms"][arm]["impermissible_coercion_bypass"]
        perm = report["arms"][arm]["permissible_legitimate_request_mishandled"]
        print(
            f"{arm:18s} {imp['rate']:6.1%} ({imp['k']}/{imp['n']})"
            f"{perm['rate']:19.1%} ({perm['k']}/{perm['n']})"
        )

    comparison = report["comparisons"]["classifier_vs_hardened_permissible"]
    print(
        "\nclassifier vs hardened prompt, permissible behavior: "
        f"{comparison['rate_difference_classifier_minus_hardened']:+.1%}; "
        f"exact paired p="
        f"{comparison['mcnemar']['two_sided_exact_p']:.4f}"
    )
    classifier_safety = report["arms"]["classifier"][
        "impermissible_coercion_bypass"
    ]
    print(
        "classifier impermissible safety ceiling: "
        f"{classifier_safety['k']}/{classifier_safety['n']}; "
        f"one-sided upper 95%="
        f"{classifier_safety['one_sided_exact_upper95']:.2%}"
    )

    destination = SUITE / "powered_results.json"
    destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"wrote {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
