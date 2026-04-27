"""Compute Tier 4 canonical science metrics + Welch's t-tests."""
import json
import math
import os
import numpy as np
from scipy import stats


def load(path):
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def get_sub_risk(s):
    return s.get("sub_risk") or s.get("factors", {}).get("behavior", "?")


def compute(scores_path, seeds_path=None):
    scores = load(scores_path)
    seeds = load(seeds_path) if seeds_path else None

    violations = []
    failure_cats = {}
    total_sub_risks = set()
    hit_sub_risks = set()
    judge_fails = 0
    overrefusals = 0

    for s in scores:
        sr = get_sub_risk(s)
        total_sub_risks.add(sr)
        if s.get("judge_status", "ok") != "ok":
            judge_fails += 1
            continue
        v = s.get("verdict", {})
        dims = v.get("dimensions", {})
        violated = dims.get("policy_violation", False)
        violations.append(1.0 if violated else 0.0)
        if violated:
            failure_cats[sr] = failure_cats.get(sr, 0) + 1
            hit_sub_risks.add(sr)
        if dims.get("overrefusal", False):
            overrefusals += 1

    n = len(violations)
    signal_rate = float(np.mean(violations)) if violations else 0.0

    # Construct coverage
    if seeds:
        seed_subs = set(get_sub_risk(s) for s in seeds)
        seed_subs.discard("?")
        construct_cov = len(hit_sub_risks) / len(seed_subs) if seed_subs else 0
        total_behaviors = len(seed_subs)
    else:
        total_sub_risks.discard("?")
        construct_cov = len(hit_sub_risks) / len(total_sub_risks) if total_sub_risks else 0
        total_behaviors = len(total_sub_risks)

    # Failure variety (Hill1)
    if failure_cats:
        total_f = sum(failure_cats.values())
        probs = [c / total_f for c in failure_cats.values()]
        entropy = -sum(p * math.log(p) for p in probs if p > 0)
        hill1 = math.exp(entropy)
    else:
        hill1 = 0.0

    return {
        "n": n, "signal_rate": signal_rate, "hill1": hill1,
        "construct_cov": construct_cov, "violations_arr": violations,
        "total_violations": sum(violations), "judge_fails": judge_fails,
        "overrefusals": overrefusals, "failure_cats": len(failure_cats),
        "total_behaviors": total_behaviors,
    }


def welch(a, b):
    if len(a) < 10 or len(b) < 10:
        return None, None
    t, p = stats.ttest_ind(a, b, equal_var=False)
    return float(t), float(p)


def verdict(p):
    if p is None:
        return "TooFewSamples"
    if p < 0.001:
        return f"Real (p<0.001)"
    if p < 0.05:
        return f"Real (p={p:.3f})"
    return f"Unsure (p={p:.2f})"


def main():
    results = {}
    configs = [
        ("old", "artifacts/comparison/t4/old/t4-old", "artifacts/comparison/t4/old/t4-old"),
        ("changliu2", "artifacts/results/t4-changliu2", "artifacts/results/t4-changliu2"),
        ("main", "artifacts/comparison/t4/main/t4-main", "artifacts/comparison/t4/main/t4-main"),
    ]

    for pipeline, prefix, seeds_prefix in configs:
        results[pipeline] = {}
        for spec in ["safety", "quality"]:
            sp = f"{prefix}-{spec}/baseline/scores.jsonl"
            seeds_candidates = [
                f"{seeds_prefix}-{spec}/seeds.jsonl",
                f"{seeds_prefix}-{spec}/baseline/seeds.jsonl",
            ]
            seeds_p = next((p for p in seeds_candidates if os.path.exists(p)), None)
            results[pipeline][spec] = compute(sp, seeds_p)

    # Print tables per spec
    for spec in ["safety", "quality"]:
        o = results["old"][spec]
        c = results["changliu2"][spec]
        m = results["main"][spec]

        print(f"\n### {spec.title()} Risk Spec (100 seeds)")
        print()
        print("| Metric | Old Science | changliu2 | origin/main | old→main (p) | old→changliu2 (p) | changliu2→main (p) |")
        print("|--------|:-----------:|:---------:|:-----------:|:------------:|:-----------------:|:------------------:|")

        # Signal Rate
        _, p_om = welch(o["violations_arr"], m["violations_arr"])
        _, p_oc = welch(o["violations_arr"], c["violations_arr"])
        _, p_cm = welch(c["violations_arr"], m["violations_arr"])
        print(f"| Mean Signal Rate | {o['signal_rate']:.3f} | {c['signal_rate']:.3f} | {m['signal_rate']:.3f} | {verdict(p_om)} | {verdict(p_oc)} | {verdict(p_cm)} |")

        # Construct Coverage (point estimate, no t-test)
        print(f"| Mean Construct Coverage | {o['construct_cov']:.3f} | {c['construct_cov']:.3f} | {m['construct_cov']:.3f} | — | — | — |")

        # Failure Variety (point estimate)
        print(f"| Mean Failure Variety | {o['hill1']:.2f} | {c['hill1']:.2f} | {m['hill1']:.2f} | — | — | — |")

        # Item Saturation and Separation Strength: N/A (single model)
        print(f"| Mean Separation Strength | N/A | N/A | N/A | — | — | — |")
        print(f"| Mean Item Saturation | N/A | N/A | N/A | — | — | — |")

        print()
        print("| Auxiliary | Old | changliu2 | main |")
        print("|----------|:---:|:---------:|:----:|")
        print(f"| Violations | {int(o['total_violations'])}/{o['n']} | {int(c['total_violations'])}/{c['n']} | {int(m['total_violations'])}/{m['n']} |")
        print(f"| Judge Failures | {o['judge_fails']} | {c['judge_fails']} | {m['judge_fails']} |")
        print(f"| Overrefusals | {o['overrefusals']} | {c['overrefusals']} | {m['overrefusals']} |")
        print(f"| Failure Categories Hit | {o['failure_cats']}/{o['total_behaviors']} | {c['failure_cats']}/{c['total_behaviors']} | {m['failure_cats']}/{m['total_behaviors']} |")

    # Summary interpretation
    print("\n### Interpretation")
    for spec in ["safety", "quality"]:
        o = results["old"][spec]
        c = results["changliu2"][spec]
        m = results["main"][spec]
        _, p_oc = welch(o["violations_arr"], c["violations_arr"])
        _, p_om = welch(o["violations_arr"], m["violations_arr"])
        _, p_cm = welch(c["violations_arr"], m["violations_arr"])
        print(f"\n**{spec.title()}:**")
        print(f"  old={o['signal_rate']:.3f}, changliu2={c['signal_rate']:.3f}, main={m['signal_rate']:.3f}")
        print(f"  old→changliu2: {verdict(p_oc)}")
        print(f"  old→main: {verdict(p_om)}")
        print(f"  changliu2→main: {verdict(p_cm)}")


if __name__ == "__main__":
    main()
