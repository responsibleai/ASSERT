"""PR Regression Gating Statistical Comparison Module

This module provides statistical functions for comparing two pipeline runs (baseline vs. treatment)
to gate pull requests based on regression in efficacy metrics.

The 6 canonical science efficacy metrics are:
  1. Construct Coverage: Fraction of constructs in the problem pool covered
  2. Separation Strength: Strength of separation between solved and unsolved problem items
  3. Signal Rate: Fraction of correctly predicted answers (TP + TN)
  4. Failure Variety: Diversity of failure modes across problem categories
  5. Item Saturation: Coverage and difficulty distribution across the problem pool
  6. Discrimination Power: Ability to distinguish between high and low ability levels

Functions:
  - paired_ttest: T-test comparison for continuous metrics
  - wilson_ci: Wilson score CI for boolean metrics
  - compare_boolean: Compare two boolean metric rates
  - gate_decision: Aggregate into PASS/WARN/BLOCK decision
"""

from typing import Optional
import numpy as np
from scipy import stats


def paired_ttest(
    baseline: list[float],
    treatment: list[float],
    alpha: float = 0.05,
    metric_direction: str = "higher_is_better"
) -> dict:
    """Perform paired t-test on continuous metrics.
    
    Args:
        baseline: Baseline metric values
        treatment: Treatment metric values
        alpha: Significance level (default 0.05)
        metric_direction: "higher_is_better" or "lower_is_better"
    
    Returns:
        Dictionary with keys:
            - effect: "Improved" | "Degraded" | "Inconclusive" | "TooFewSamples"
            - p_value: Two-tailed p-value (None if TooFewSamples)
            - mean_diff: mean(treatment) - mean(baseline)
            - ci_lower: 95% CI lower bound (None if TooFewSamples)
            - ci_upper: 95% CI upper bound (None if TooFewSamples)
            - n: Number of paired samples
    """
    baseline_arr = np.array(baseline)
    treatment_arr = np.array(treatment)
    n = len(baseline_arr)
    
    # Rule: TooFewSamples if n < 10
    if n < 10:
        return {
            "effect": "TooFewSamples",
            "p_value": None,
            "mean_diff": float(np.mean(treatment_arr) - np.mean(baseline_arr)),
            "ci_lower": None,
            "ci_upper": None,
            "n": n
        }
    
    # Compute differences
    diffs = treatment_arr - baseline_arr
    mean_diff = float(np.mean(diffs))
    std_diff = float(np.std(diffs, ddof=1))  # sample std dev
    
    # Paired t-test
    t_stat, p_value = stats.ttest_rel(treatment_arr, baseline_arr)
    
    # Confidence interval: mean_diff ± t_critical * (std_diff / sqrt(n))
    t_critical = stats.t.ppf(1 - alpha / 2, df=n - 1)
    se = std_diff / np.sqrt(n)
    ci_lower = float(mean_diff - t_critical * se)
    ci_upper = float(mean_diff + t_critical * se)
    
    # Determine effect
    if p_value > alpha:
        effect = "Inconclusive"
    else:
        if metric_direction == "higher_is_better":
            effect = "Improved" if mean_diff > 0 else "Degraded"
        else:  # lower_is_better
            effect = "Improved" if mean_diff < 0 else "Degraded"
    
    return {
        "effect": effect,
        "p_value": float(p_value),
        "mean_diff": mean_diff,
        "ci_lower": ci_lower,
        "ci_upper": ci_upper,
        "n": n
    }


def wilson_ci(successes: int, total: int, alpha: float = 0.05) -> dict:
    """Compute Wilson score confidence interval for boolean metrics.
    
    The Wilson score interval is recommended for binomial proportions as it has
    better coverage properties than standard intervals, especially for extreme rates.
    
    Args:
        successes: Number of successes
        total: Total number of trials
        alpha: Significance level (default 0.05)
    
    Returns:
        Dictionary with keys:
            - rate: successes / total
            - ci_lower: Lower bound of CI
            - ci_upper: Upper bound of CI
            - n: Total number of trials
    """
    if total == 0:
        return {
            "rate": 0.0,
            "ci_lower": 0.0,
            "ci_upper": 0.0,
            "n": 0
        }
    
    p = successes / total
    z = stats.norm.ppf(1 - alpha / 2)
    z_sq = z * z
    
    denominator = 1 + z_sq / total
    centre = (p + z_sq / (2 * total)) / denominator
    margin = z * np.sqrt(p * (1 - p) / total + z_sq / (4 * total * total)) / denominator
    
    ci_lower = float(centre - margin)
    ci_upper = float(centre + margin)
    
    return {
        "rate": float(p),
        "ci_lower": max(0.0, ci_lower),
        "ci_upper": min(1.0, ci_upper),
        "n": total
    }


def compare_boolean(
    baseline_pass: int,
    baseline_total: int,
    treatment_pass: int,
    treatment_total: int,
    alpha: float = 0.05
) -> dict:
    """Compare two boolean metric rates using Wilson score intervals.
    
    Args:
        baseline_pass: Number of passes in baseline
        baseline_total: Total trials in baseline
        treatment_pass: Number of passes in treatment
        treatment_total: Total trials in treatment
        alpha: Significance level (default 0.05)
    
    Returns:
        Dictionary with keys:
            - effect: "Improved" | "Degraded" | "Inconclusive" | "TooFewSamples"
            - baseline_rate: Baseline pass rate
            - treatment_rate: Treatment pass rate
            - baseline_ci: Wilson CI dict for baseline
            - treatment_ci: Wilson CI dict for treatment
    """
    baseline_ci = wilson_ci(baseline_pass, baseline_total, alpha)
    treatment_ci = wilson_ci(treatment_pass, treatment_total, alpha)
    
    # Rule: TooFewSamples if either total < 10
    if baseline_total < 10 or treatment_total < 10:
        return {
            "effect": "TooFewSamples",
            "baseline_rate": baseline_ci["rate"],
            "treatment_rate": treatment_ci["rate"],
            "baseline_ci": baseline_ci,
            "treatment_ci": treatment_ci
        }
    
    # Rule: Degraded if treatment CI upper < baseline CI lower
    if treatment_ci["ci_upper"] < baseline_ci["ci_lower"]:
        effect = "Degraded"
    # Rule: Improved if treatment CI lower > baseline CI upper
    elif treatment_ci["ci_lower"] > baseline_ci["ci_upper"]:
        effect = "Improved"
    # Rule: Inconclusive if overlapping CIs
    else:
        effect = "Inconclusive"
    
    return {
        "effect": effect,
        "baseline_rate": baseline_ci["rate"],
        "treatment_rate": treatment_ci["rate"],
        "baseline_ci": baseline_ci,
        "treatment_ci": treatment_ci
    }


def gate_decision(results: list[dict]) -> dict:
    """Aggregate individual metric comparisons into a PR gate decision.
    
    Args:
        results: List of dicts from paired_ttest/compare_boolean with added
                 "metric_name" and "category" keys.
                 category is "science" or "engineering"
    
    Returns:
        Dictionary with keys:
            - decision: "PASS" | "WARN" | "BLOCK"
            - reasons: List of reason strings explaining the decision
    
    Decision rules:
      - BLOCK if any science metric has effect="Degraded"
      - BLOCK if any engineering metric has effect="Degraded"
      - WARN if any metric has effect="TooFewSamples"
      - WARN if any metric has effect="Inconclusive" with negative trend
      - PASS otherwise
    """
    reasons = []
    decision = "PASS"
    
    for result in results:
        metric_name = result.get("metric_name", "unknown")
        category = result.get("category", "unknown")
        effect = result.get("effect", "unknown")
        
        # Rule: BLOCK if any science metric has effect="Degraded"
        if category == "science" and effect == "Degraded":
            decision = "BLOCK"
            reasons.append(f"REGRESSION: Science metric '{metric_name}' DEGRADED")
        
        # Rule: BLOCK if any engineering metric has effect="Degraded"
        elif category == "engineering" and effect == "Degraded":
            decision = "BLOCK"
            reasons.append(f"REGRESSION: Engineering metric '{metric_name}' DEGRADED")
        
        # Rule: WARN if any metric has effect="TooFewSamples"
        elif effect == "TooFewSamples":
            if decision != "BLOCK":
                decision = "WARN"
            n = result.get("n", "?")
            reasons.append(f"WARNING: Metric '{metric_name}' has TooFewSamples (n={n}, need n>=10)")
        
        # Rule: WARN if inconclusive with negative trend
        elif effect == "Inconclusive":
            # Determine if negative trend
            has_negative_trend = False
            
            # For continuous metrics, check mean_diff
            if "mean_diff" in result:
                mean_diff = result.get("mean_diff")
                if mean_diff is not None and mean_diff < 0:
                    has_negative_trend = True
            
            # For boolean metrics, check rate comparison
            elif "treatment_rate" in result:
                baseline_rate = result.get("baseline_rate")
                treatment_rate = result.get("treatment_rate")
                if baseline_rate is not None and treatment_rate is not None and treatment_rate < baseline_rate:
                    has_negative_trend = True
            
            if has_negative_trend:
                if decision != "BLOCK":
                    decision = "WARN"
                reasons.append(f"WARNING: Metric '{metric_name}' is INCONCLUSIVE with negative trend")
    
    return {
        "decision": decision,
        "reasons": reasons
    }


if __name__ == "__main__":
    # Demo: Sample baseline and treatment data
    print("=" * 78)
    print("PR Regression Gating: Example Analysis")
    print("=" * 78)
    
    # Example 1: Continuous metric (e.g., construct coverage)
    print("\n[1] Paired t-test on Construct Coverage (continuous metric)")
    print("-" * 78)
    baseline_coverage = [0.75, 0.78, 0.76, 0.80, 0.79, 0.77, 0.78, 0.81, 0.76, 0.79,
                        0.77, 0.75, 0.80, 0.78, 0.76]
    treatment_coverage = [0.76, 0.79, 0.77, 0.81, 0.80, 0.78, 0.79, 0.82, 0.77, 0.80,
                         0.78, 0.76, 0.81, 0.79, 0.77]
    
    result_coverage = paired_ttest(baseline_coverage, treatment_coverage)
    print(f"  Baseline mean:    {np.mean(baseline_coverage):.4f}")
    print(f"  Treatment mean:   {np.mean(treatment_coverage):.4f}")
    print(f"  Mean diff:        {result_coverage['mean_diff']:.4f}")
    print(f"  95% CI:           [{result_coverage['ci_lower']:.4f}, {result_coverage['ci_upper']:.4f}]")
    print(f"  p-value:          {result_coverage['p_value']:.6f}")
    print(f"  Sample size:      {result_coverage['n']}")
    print(f"  Effect:           {result_coverage['effect']}")
    
    # Example 2: Continuous metric with degradation
    print("\n[2] Paired t-test on Separation Strength (continuous metric)")
    print("-" * 78)
    baseline_separation = [0.85, 0.83, 0.84, 0.86, 0.82, 0.85, 0.84, 0.83, 0.85, 0.84,
                          0.83, 0.82, 0.84, 0.85, 0.83]
    treatment_separation = [0.82, 0.81, 0.80, 0.83, 0.79, 0.82, 0.81, 0.80, 0.82, 0.81,
                           0.80, 0.79, 0.81, 0.82, 0.80]
    
    result_separation = paired_ttest(baseline_separation, treatment_separation)
    print(f"  Baseline mean:    {np.mean(baseline_separation):.4f}")
    print(f"  Treatment mean:   {np.mean(treatment_separation):.4f}")
    print(f"  Mean diff:        {result_separation['mean_diff']:.4f}")
    print(f"  95% CI:           [{result_separation['ci_lower']:.4f}, {result_separation['ci_upper']:.4f}]")
    print(f"  p-value:          {result_separation['p_value']:.6f}")
    print(f"  Sample size:      {result_separation['n']}")
    print(f"  Effect:           {result_separation['effect']}")
    
    # Example 3: Boolean metric (e.g., judge pass rate)
    print("\n[3] Boolean metric comparison: Judge Pass Rate")
    print("-" * 78)
    baseline_pass, baseline_total = 85, 100
    treatment_pass, treatment_total = 88, 100
    
    result_judge = compare_boolean(baseline_pass, baseline_total, treatment_pass, treatment_total)
    print(f"  Baseline: {baseline_pass}/{baseline_total} ({result_judge['baseline_rate']:.2%})")
    print(f"    CI: [{result_judge['baseline_ci']['ci_lower']:.4f}, {result_judge['baseline_ci']['ci_upper']:.4f}]")
    print(f"  Treatment: {treatment_pass}/{treatment_total} ({result_judge['treatment_rate']:.2%})")
    print(f"    CI: [{result_judge['treatment_ci']['ci_lower']:.4f}, {result_judge['treatment_ci']['ci_upper']:.4f}]")
    print(f"  Effect:           {result_judge['effect']}")
    
    # Example 4: Boolean metric with degradation
    print("\n[4] Boolean metric comparison: Signal Rate (degraded)")
    print("-" * 78)
    baseline_signal, baseline_signal_total = 92, 100
    treatment_signal, treatment_signal_total = 88, 100
    
    result_signal = compare_boolean(baseline_signal, baseline_signal_total, 
                                    treatment_signal, treatment_signal_total)
    print(f"  Baseline: {baseline_signal}/{baseline_signal_total} ({result_signal['baseline_rate']:.2%})")
    print(f"    CI: [{result_signal['baseline_ci']['ci_lower']:.4f}, {result_signal['baseline_ci']['ci_upper']:.4f}]")
    print(f"  Treatment: {treatment_signal}/{treatment_signal_total} ({result_signal['treatment_rate']:.2%})")
    print(f"    CI: [{result_signal['treatment_ci']['ci_lower']:.4f}, {result_signal['treatment_ci']['ci_upper']:.4f}]")
    print(f"  Effect:           {result_signal['effect']}")
    
    # Example 5: Aggregate gate decision
    print("\n[5] Aggregate Gate Decision (multiple metrics)")
    print("-" * 78)
    
    # Annotate results with metadata
    result_coverage["metric_name"] = "Construct Coverage"
    result_coverage["category"] = "science"
    
    result_separation["metric_name"] = "Separation Strength"
    result_separation["category"] = "science"
    
    result_judge["metric_name"] = "Judge Pass Rate"
    result_judge["category"] = "engineering"
    
    result_signal["metric_name"] = "Signal Rate"
    result_signal["category"] = "science"
    
    all_results = [result_coverage, result_separation, result_judge, result_signal]
    gate = gate_decision(all_results)
    
    print(f"  Decision:         {gate['decision']}")
    print(f"\n  Reasons:")
    for reason in gate['reasons']:
        print(f"    • {reason}")
    
    print("\n" + "=" * 78)
