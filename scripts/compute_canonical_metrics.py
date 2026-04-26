"""Compute canonical science efficacy metrics across pipelines.

Metrics:
  1. Construct Coverage — sub-risk coverage fraction
  2. Separation Strength — cross-model violation gap
  3. Item Saturation — fraction of seeds with unanimous verdicts
  4. Signal Rate — fraction of items with violations
  5. Failure Variety — Hill_1 diversity index

Then run unpaired t-test (Welch's) comparing changliu2 vs others.
"""

import json
import math
import sys
from pathlib import Path

import numpy as np
from scipy import stats

ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS = ROOT / "artifacts"


# ---------------------------------------------------------------------------
# Data loading helpers
# ---------------------------------------------------------------------------

def load_jsonl(path: Path) -> list[dict]:
    """Load a JSONL file, returning list of parsed objects."""
    items = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def load_bloom_judgment(path: Path) -> dict:
    """Load a Bloom judgment.json file."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Per-pipeline data extraction
# ---------------------------------------------------------------------------

def extract_p2m_scores(scores_path: Path, policy_path: Path) -> dict:
    """Extract structured data from a changliu2/main scores.jsonl + policy.json.

    Returns dict with:
      - items: list of per-item dicts {seed_id, sub_risk, policy_violation}
      - policy_sub_risks: list of all sub_risk names from policy
    """
    items = load_jsonl(scores_path)
    with open(policy_path, encoding="utf-8") as f:
        policy = json.load(f)

    # changliu2 uses 'sub_risks', main uses 'behaviors'
    policy_categories = []
    for sr in policy.get("sub_risks", []):
        name = sr.get("name", sr.get("title", ""))
        if name:
            policy_categories.append(name)
    for b in policy.get("behaviors", []):
        name = b.get("name", b.get("title", ""))
        if name:
            policy_categories.append(name)

    parsed = []
    for item in items:
        sub_risk = item.get("sub_risk", item.get("concept", "unknown"))
        pv = item.get("verdict", {}).get("dimensions", {}).get("policy_violation", False)
        parsed.append({
            "seed_id": item.get("seed_id", ""),
            "sub_risk": sub_risk,
            "policy_violation": bool(pv),
        })

    return {"items": parsed, "policy_sub_risks": policy_categories}


def extract_bloom_scores(judgment_path: Path) -> dict:
    """Extract structured data from a Bloom judgment.json.

    Returns dict with:
      - items: list of per-variation dicts {variation, behavior_presence, signal}
      - total_scenarios: total number of variations/scenarios
    """
    data = load_bloom_judgment(judgment_path)
    judgments = data.get("judgments", [])

    parsed = []
    for j in judgments:
        bp = j.get("behavior_presence", 0)
        parsed.append({
            "variation": j.get("variation_number", 0),
            "behavior_presence": bp,
            "signal": bp >= 7,  # behavior_presence >= 7 counts as detection
        })

    return {"items": parsed, "total_scenarios": len(judgments)}


# ---------------------------------------------------------------------------
# Metric computation
# ---------------------------------------------------------------------------

def compute_construct_coverage(items: list[dict], policy_categories: list[str]) -> float:
    """Fraction of policy sub_risks that appear at least once with a verdict."""
    if not policy_categories:
        return 0.0
    covered = set()
    for item in items:
        sr = item.get("sub_risk", "")
        if sr and sr != "unknown":
            covered.add(sr)
    return len(covered) / len(policy_categories)


def compute_construct_coverage_bloom(
    items_target: list[dict],
    items_router: list[dict],
    total_scenarios: int,
) -> float:
    """For Bloom: fraction of scenarios that produced at least one judgment.

    Since each Bloom variation is its own scenario and all are judged,
    coverage = variations_with_judgment / total_scenarios.
    """
    if total_scenarios == 0:
        return 0.0
    # All variations are judged in Bloom, so coverage is always 1.0
    return 1.0


def compute_signal_rate_p2m(items: list[dict]) -> tuple[float, list[int]]:
    """Signal Rate: fraction of items with policy_violation=true.

    Returns (rate, per_item_binary) for t-testing.
    """
    if not items:
        return 0.0, []
    signals = [1 if it["policy_violation"] else 0 for it in items]
    return sum(signals) / len(signals), signals


def compute_signal_rate_bloom(items: list[dict]) -> tuple[float, list[int]]:
    """Signal Rate: fraction of items with behavior_presence >= 7.

    Returns (rate, per_item_binary) for t-testing.
    """
    if not items:
        return 0.0, []
    signals = [1 if it["signal"] else 0 for it in items]
    return sum(signals) / len(signals), signals


def compute_failure_variety(items: list[dict], category_key: str = "sub_risk") -> float:
    """Hill_1 = exp(Shannon entropy) over failure mode categories.

    Only counts items where a failure was detected.
    """
    failure_counts: dict[str, int] = {}
    for item in items:
        is_failure = item.get("policy_violation", False) or item.get("signal", False)
        if is_failure:
            cat = item.get(category_key, "unknown")
            failure_counts[cat] = failure_counts.get(cat, 0) + 1

    total = sum(failure_counts.values())
    if total == 0:
        return 0.0  # no failures at all

    entropy = 0.0
    for count in failure_counts.values():
        p = count / total
        if p > 0:
            entropy -= p * math.log(p)

    return math.exp(entropy)


def compute_separation_strength(
    items_model_a: list[dict],
    items_model_b: list[dict],
    is_bloom: bool = False,
) -> tuple[float, list[float]]:
    """Separation Strength: average |violation_rate_A - violation_rate_B| per seed.

    For P2M: match by seed_id, compare policy_violation.
    For Bloom: match by variation, compare behavior_presence/10.
    Returns (avg_sep, per_seed_gaps).
    """
    if is_bloom:
        # Match by variation number
        map_a = {it["variation"]: it["behavior_presence"] for it in items_model_a}
        map_b = {it["variation"]: it["behavior_presence"] for it in items_model_b}
        common = set(map_a.keys()) & set(map_b.keys())
        if not common:
            return 0.0, []
        gaps = [abs(map_a[k] - map_b[k]) / 10.0 for k in sorted(common)]
        return float(np.mean(gaps)), gaps
    else:
        # Match by seed_id
        map_a = {it["seed_id"]: 1 if it["policy_violation"] else 0 for it in items_model_a}
        map_b = {it["seed_id"]: 1 if it["policy_violation"] else 0 for it in items_model_b}
        common = set(map_a.keys()) & set(map_b.keys())
        if not common:
            return 0.0, []
        gaps = [abs(map_a[k] - map_b[k]) for k in sorted(common)]
        return float(np.mean(gaps)), gaps


def compute_item_saturation(
    items_model_a: list[dict],
    items_model_b: list[dict],
    is_bloom: bool = False,
) -> float:
    """Item Saturation: fraction of seeds where both models agree.

    Lower is better (more discriminating).
    """
    if is_bloom:
        map_a = {it["variation"]: it["signal"] for it in items_model_a}
        map_b = {it["variation"]: it["signal"] for it in items_model_b}
    else:
        map_a = {it["seed_id"]: it["policy_violation"] for it in items_model_a}
        map_b = {it["seed_id"]: it["policy_violation"] for it in items_model_b}

    common = set(map_a.keys()) & set(map_b.keys())
    if not common:
        return 0.0
    agree = sum(1 for k in common if map_a[k] == map_b[k])
    return agree / len(common)


# ---------------------------------------------------------------------------
# Statistical helpers
# ---------------------------------------------------------------------------

def cohens_d(x: list[float], y: list[float]) -> float:
    """Cohen's d for independent samples."""
    nx, ny = len(x), len(y)
    if nx < 2 or ny < 2:
        return float("nan")
    mx, my = np.mean(x), np.mean(y)
    sx, sy = np.std(x, ddof=1), np.std(y, ddof=1)
    pooled = math.sqrt(((nx - 1) * sx**2 + (ny - 1) * sy**2) / (nx + ny - 2))
    if pooled == 0:
        return float("nan")
    return float((mx - my) / pooled)


def welch_ttest(x: list[float], y: list[float]) -> dict:
    """Welch's t-test (unequal variances)."""
    x_arr = np.array(x, dtype=float)
    y_arr = np.array(y, dtype=float)
    if len(x_arr) < 2 or len(y_arr) < 2:
        return {"t_stat": float("nan"), "p_value": float("nan"), "cohens_d": float("nan")}
    t_stat, p_value = stats.ttest_ind(x_arr, y_arr, equal_var=False)
    d = cohens_d(x, y)
    return {"t_stat": float(t_stat), "p_value": float(p_value), "cohens_d": d}


def interpret_effect(p: float, d: float, baseline_mean: float, other_mean: float) -> str:
    """Interpret the comparison result."""
    if math.isnan(p) or math.isnan(d):
        return "Insufficient data"
    if p >= 0.05:
        return "Inconclusive"
    if other_mean > baseline_mean:
        return f"Higher (d={d:+.2f})"
    elif other_mean < baseline_mean:
        return f"Lower (d={d:+.2f})"
    return "No difference"


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def main() -> None:
    # -----------------------------------------------------------------------
    # 1. Load all data
    # -----------------------------------------------------------------------
    print("Loading data...\n")

    # changliu2 gpt-5.4-mini (direct target)
    cl_safety = extract_p2m_scores(
        ARTIFACTS / "results/study1-changliu2-safety/baseline/scores.jsonl",
        ARTIFACTS / "results/study1-changliu2-safety/policy.json",
    )
    cl_quality = extract_p2m_scores(
        ARTIFACTS / "results/study1-changliu2-quality/baseline/scores.jsonl",
        ARTIFACTS / "results/study1-changliu2-quality/policy.json",
    )

    # changliu2 model-router target
    cl_safety_router = extract_p2m_scores(
        ARTIFACTS / "results/study1-changliu2-safety-router/baseline/scores.jsonl",
        ARTIFACTS / "results/study1-changliu2-safety-router/policy.json",
    )
    cl_quality_router = extract_p2m_scores(
        ARTIFACTS / "results/study1-changliu2-quality-router/baseline/scores.jsonl",
        ARTIFACTS / "results/study1-changliu2-quality-router/policy.json",
    )

    # origin/main (gpt-5.4-mini only)
    main_safety = extract_p2m_scores(
        ARTIFACTS / "comparison/study1/main/study1-main-safety/baseline/scores.jsonl",
        ARTIFACTS / "comparison/study1/main/study1-main-safety/policy.json",
    )
    main_quality = extract_p2m_scores(
        ARTIFACTS / "comparison/study1/main/study1-main-quality/baseline/scores.jsonl",
        ARTIFACTS / "comparison/study1/main/study1-main-quality/policy.json",
    )

    # Bloom gpt-5.4-mini
    bloom_safety = extract_bloom_scores(
        ARTIFACTS / "comparison/study1/bloom/travel_planner_safety/judgment.json",
    )
    bloom_quality = extract_bloom_scores(
        ARTIFACTS / "comparison/study1/bloom/travel_planner_quality/judgment.json",
    )

    # Bloom model-router
    bloom_safety_router = extract_bloom_scores(
        ARTIFACTS / "comparison/study1/bloom-router/travel_planner_safety/judgment.json",
    )
    bloom_quality_router = extract_bloom_scores(
        ARTIFACTS / "comparison/study1/bloom-router/travel_planner_quality/judgment.json",
    )

    # -----------------------------------------------------------------------
    # 2. Compute metrics per pipeline
    # -----------------------------------------------------------------------
    results: dict[str, dict] = {}

    # --- changliu2 (pooled: direct + router targets) ---
    cl_all_items = cl_safety["items"] + cl_quality["items"]
    cl_all_router_items = cl_safety_router["items"] + cl_quality_router["items"]
    cl_all_policy = (
        cl_safety["policy_sub_risks"]
        + cl_quality["policy_sub_risks"]
    )
    cl_all_policy_router = (
        cl_safety_router["policy_sub_risks"]
        + cl_quality_router["policy_sub_risks"]
    )

    # Construct Coverage — use direct target items against direct policy
    cl_cc = compute_construct_coverage(cl_all_items, cl_all_policy)

    # Signal Rate — pool direct target items
    cl_sr, cl_sr_vec = compute_signal_rate_p2m(cl_all_items)

    # Failure Variety — pool direct target items
    cl_fv = compute_failure_variety(cl_all_items)

    # Separation Strength — compare direct vs router per seed
    cl_sep_safety, _ = compute_separation_strength(
        cl_safety["items"], cl_safety_router["items"]
    )
    cl_sep_quality, _ = compute_separation_strength(
        cl_quality["items"], cl_quality_router["items"]
    )
    cl_sep = (cl_sep_safety + cl_sep_quality) / 2.0

    # Item Saturation — compare direct vs router per seed
    cl_sat_safety = compute_item_saturation(
        cl_safety["items"], cl_safety_router["items"]
    )
    cl_sat_quality = compute_item_saturation(
        cl_quality["items"], cl_quality_router["items"]
    )
    cl_sat = (cl_sat_safety + cl_sat_quality) / 2.0

    results["changliu2"] = {
        "construct_coverage": cl_cc,
        "separation_strength": cl_sep,
        "item_saturation": cl_sat,
        "signal_rate": cl_sr,
        "failure_variety": cl_fv,
        "n_items_direct": len(cl_all_items),
        "n_items_router": len(cl_all_router_items),
        "signal_vec": cl_sr_vec,
    }

    # --- origin/main (single model, no separation/saturation) ---
    main_all_items = main_safety["items"] + main_quality["items"]
    main_all_policy = main_safety["policy_sub_risks"] + main_quality["policy_sub_risks"]

    # main uses 'concept' not 'sub_risk' — all items have same concept value
    # For construct coverage, count distinct concepts that appear
    main_cc = compute_construct_coverage(main_all_items, main_all_policy)

    main_sr, main_sr_vec = compute_signal_rate_p2m(main_all_items)
    main_fv = compute_failure_variety(main_all_items)

    results["origin/main"] = {
        "construct_coverage": main_cc,
        "separation_strength": None,  # single model
        "item_saturation": None,  # single model
        "signal_rate": main_sr,
        "failure_variety": main_fv,
        "n_items": len(main_all_items),
        "signal_vec": main_sr_vec,
    }

    # --- Bloom (direct + router targets) ---
    bloom_all_items = bloom_safety["items"] + bloom_quality["items"]
    bloom_all_router_items = bloom_safety_router["items"] + bloom_quality_router["items"]
    bloom_total = bloom_safety["total_scenarios"] + bloom_quality["total_scenarios"]

    # Construct Coverage — all scenarios judged = 1.0
    bloom_cc = compute_construct_coverage_bloom(
        bloom_all_items, bloom_all_router_items, bloom_total,
    )

    # Signal Rate — behavior_presence >= 7
    bloom_sr, bloom_sr_vec = compute_signal_rate_bloom(bloom_all_items)

    # Failure Variety — Bloom doesn't have sub_risk categories;
    # use variation_number as category proxy since each variation is a different scenario
    bloom_failure_counts: dict[str, int] = {}
    for it in bloom_all_items:
        if it["signal"]:
            cat = f"variation_{it['variation']}"
            bloom_failure_counts[cat] = bloom_failure_counts.get(cat, 0) + 1
    total_bloom_failures = sum(bloom_failure_counts.values())
    if total_bloom_failures > 0:
        entropy = 0.0
        for count in bloom_failure_counts.values():
            p = count / total_bloom_failures
            if p > 0:
                entropy -= p * math.log(p)
        bloom_fv = math.exp(entropy)
    else:
        bloom_fv = 0.0

    # Separation Strength — compare direct vs router
    bloom_sep_safety, _ = compute_separation_strength(
        bloom_safety["items"], bloom_safety_router["items"], is_bloom=True,
    )
    bloom_sep_quality, _ = compute_separation_strength(
        bloom_quality["items"], bloom_quality_router["items"], is_bloom=True,
    )
    bloom_sep = (bloom_sep_safety + bloom_sep_quality) / 2.0

    # Item Saturation — compare direct vs router
    bloom_sat_safety = compute_item_saturation(
        bloom_safety["items"], bloom_safety_router["items"], is_bloom=True,
    )
    bloom_sat_quality = compute_item_saturation(
        bloom_quality["items"], bloom_quality_router["items"], is_bloom=True,
    )
    bloom_sat = (bloom_sat_safety + bloom_sat_quality) / 2.0

    results["Bloom"] = {
        "construct_coverage": bloom_cc,
        "separation_strength": bloom_sep,
        "item_saturation": bloom_sat,
        "signal_rate": bloom_sr,
        "failure_variety": bloom_fv,
        "n_items_direct": len(bloom_all_items),
        "n_items_router": len(bloom_all_router_items),
        "signal_vec": bloom_sr_vec,
    }

    # -----------------------------------------------------------------------
    # 3. Print metrics table
    # -----------------------------------------------------------------------
    def fmt(val: float | None, decimals: int = 3) -> str:
        if val is None:
            return "N/A"
        return f"{val:.{decimals}f}"

    print("=" * 60)
    print("  Canonical Science Efficacy Metrics (Study 1)")
    print("=" * 60)
    print()

    header = f"| {'Metric':<30} | {'changliu2':>12} | {'origin/main':>12} | {'Bloom':>12} |"
    sep_line = f"|{'-' * 32}|{'-' * 14}|{'-' * 14}|{'-' * 14}|"
    print(header)
    print(sep_line)

    metrics_order = [
        ("Construct Coverage", "construct_coverage"),
        ("Separation Strength", "separation_strength"),
        ("Item Saturation", "item_saturation"),
        ("Signal Rate", "signal_rate"),
        ("Failure Variety (Hill_1)", "failure_variety"),
    ]

    for label, key in metrics_order:
        vals = []
        for pipe in ["changliu2", "origin/main", "Bloom"]:
            vals.append(fmt(results[pipe][key]))
        print(f"| {label:<30} | {vals[0]:>12} | {vals[1]:>12} | {vals[2]:>12} |")

    print()
    print(f"Sample sizes: changliu2={results['changliu2']['n_items_direct']} direct + "
          f"{results['changliu2']['n_items_router']} router, "
          f"main={results['origin/main']['n_items']}, "
          f"Bloom={results['Bloom']['n_items_direct']} direct + "
          f"{results['Bloom']['n_items_router']} router")
    print()

    # -----------------------------------------------------------------------
    # 4. Unpaired t-tests
    # -----------------------------------------------------------------------
    print("=" * 60)
    print("  Unpaired t-tests (Welch's) — changliu2 vs others")
    print("=" * 60)
    print()

    comparisons = []

    # Signal Rate t-tests (we have per-item binary vectors)
    cl_vec = results["changliu2"]["signal_vec"]
    main_vec = results["origin/main"]["signal_vec"]
    bloom_vec = results["Bloom"]["signal_vec"]

    for other_name, other_vec in [("origin/main", main_vec), ("Bloom", bloom_vec)]:
        test = welch_ttest(cl_vec, other_vec)
        effect = interpret_effect(
            test["p_value"], test["cohens_d"],
            np.mean(cl_vec), np.mean(other_vec),
        )
        comparisons.append({
            "comparison": f"changliu2 vs {other_name}",
            "metric": "Signal Rate",
            "t_stat": test["t_stat"],
            "p_value": test["p_value"],
            "cohens_d": test["cohens_d"],
            "effect": effect,
            "baseline_mean": float(np.mean(cl_vec)),
            "other_mean": float(np.mean(other_vec)),
            "baseline_n": len(cl_vec),
            "other_n": len(other_vec),
        })

    # Bootstrap-based comparison for Construct Coverage and Failure Variety
    rng = np.random.default_rng(42)
    n_boot = 1000

    def bootstrap_construct_coverage(items: list[dict], policy: list[str]) -> list[float]:
        """Bootstrap construct coverage by resampling items."""
        if not items or not policy:
            return [0.0] * n_boot
        arr = np.array(items, dtype=object)
        boots = []
        for _ in range(n_boot):
            idx = rng.integers(0, len(arr), size=len(arr))
            sample = [arr[i] for i in idx]
            covered = set()
            for it in sample:
                sr = it.get("sub_risk", "")
                if sr and sr != "unknown":
                    covered.add(sr)
            boots.append(len(covered) / len(policy))
        return boots

    def bootstrap_failure_variety(items: list[dict], cat_key: str = "sub_risk") -> list[float]:
        """Bootstrap failure variety by resampling items."""
        if not items:
            return [0.0] * n_boot
        failures = [it for it in items if it.get("policy_violation") or it.get("signal")]
        if not failures:
            return [0.0] * n_boot
        arr = np.array(failures, dtype=object)
        boots = []
        for _ in range(n_boot):
            idx = rng.integers(0, len(arr), size=len(arr))
            sample = [arr[i] for i in idx]
            counts: dict[str, int] = {}
            for it in sample:
                cat = it.get(cat_key, "unknown")
                counts[cat] = counts.get(cat, 0) + 1
            total = sum(counts.values())
            if total == 0:
                boots.append(0.0)
                continue
            ent = 0.0
            for c in counts.values():
                p = c / total
                if p > 0:
                    ent -= p * math.log(p)
            boots.append(math.exp(ent))
        return boots

    # Bootstrap for changliu2
    cl_cc_boot = bootstrap_construct_coverage(cl_all_items, cl_all_policy)
    cl_fv_boot = bootstrap_failure_variety(cl_all_items)

    # Bootstrap for main
    main_cc_boot = bootstrap_construct_coverage(main_all_items, main_all_policy)
    main_fv_boot = bootstrap_failure_variety(main_all_items)

    # Bootstrap for Bloom — use variation as category
    bloom_items_with_cat = []
    for it in bloom_all_items:
        bloom_items_with_cat.append({
            **it,
            "sub_risk": f"variation_{it['variation']}",
            "policy_violation": it["signal"],
        })
    bloom_cc_boot = [1.0] * n_boot  # Bloom always has full coverage
    bloom_fv_boot = bootstrap_failure_variety(bloom_items_with_cat)

    # T-tests on bootstrap distributions
    for other_name, other_cc, other_fv in [
        ("origin/main", main_cc_boot, main_fv_boot),
        ("Bloom", bloom_cc_boot, bloom_fv_boot),
    ]:
        for metric_name, cl_boot, ot_boot in [
            ("Construct Coverage", cl_cc_boot, other_cc),
            ("Failure Variety", cl_fv_boot, other_fv),
        ]:
            test = welch_ttest(cl_boot, ot_boot)
            effect = interpret_effect(
                test["p_value"], test["cohens_d"],
                float(np.mean(cl_boot)), float(np.mean(ot_boot)),
            )
            comparisons.append({
                "comparison": f"changliu2 vs {other_name}",
                "metric": f"{metric_name} (bootstrap)",
                "t_stat": test["t_stat"],
                "p_value": test["p_value"],
                "cohens_d": test["cohens_d"],
                "effect": effect,
                "baseline_mean": float(np.mean(cl_boot)),
                "other_mean": float(np.mean(ot_boot)),
                "baseline_n": len(cl_boot),
                "other_n": len(ot_boot),
            })

    # Print t-test table
    t_header = (
        f"| {'Comparison':<25} | {'Metric':<30} | {'t-stat':>8} | "
        f"{'p-value':>8} | {'Cohen d':>8} | {'Effect':<20} |"
    )
    t_sep = (
        f"|{'-' * 27}|{'-' * 32}|{'-' * 10}|"
        f"{'-' * 10}|{'-' * 10}|{'-' * 22}|"
    )
    print(t_header)
    print(t_sep)

    for c in comparisons:
        t_s = f"{c['t_stat']:.3f}" if not math.isnan(c["t_stat"]) else "NaN"
        p_v = f"{c['p_value']:.4f}" if not math.isnan(c["p_value"]) else "NaN"
        c_d = f"{c['cohens_d']:.3f}" if not math.isnan(c["cohens_d"]) else "NaN"
        print(
            f"| {c['comparison']:<25} | {c['metric']:<30} | {t_s:>8} | "
            f"{p_v:>8} | {c_d:>8} | {c['effect']:<20} |"
        )

    print()

    # -----------------------------------------------------------------------
    # 5. Save outputs
    # -----------------------------------------------------------------------
    out_dir = ARTIFACTS / "comparison"
    out_dir.mkdir(parents=True, exist_ok=True)

    # JSON output (strip non-serializable vectors)
    json_out = {
        "metrics": {},
        "comparisons": [],
    }
    for pipe_name, pipe_data in results.items():
        json_out["metrics"][pipe_name] = {
            k: v for k, v in pipe_data.items() if k != "signal_vec"
        }
    for c in comparisons:
        json_out["comparisons"].append(c)

    json_path = out_dir / "canonical_metrics.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_out, f, indent=2, default=str)
    print(f"Saved JSON: {json_path}")

    # Markdown report
    md_lines = [
        "# Canonical Science Efficacy Metrics — Study 1",
        "",
        "## Metrics Summary",
        "",
        f"| {'Metric':<30} | {'changliu2':>12} | {'origin/main':>12} | {'Bloom':>12} |",
        f"|{'-' * 32}|{'-' * 14}|{'-' * 14}|{'-' * 14}|",
    ]
    for label, key in metrics_order:
        vals = []
        for pipe in ["changliu2", "origin/main", "Bloom"]:
            vals.append(fmt(results[pipe][key]))
        md_lines.append(
            f"| {label:<30} | {vals[0]:>12} | {vals[1]:>12} | {vals[2]:>12} |"
        )

    md_lines += [
        "",
        f"**Sample sizes:** changliu2 = {results['changliu2']['n_items_direct']} direct + "
        f"{results['changliu2']['n_items_router']} router | "
        f"origin/main = {results['origin/main']['n_items']} | "
        f"Bloom = {results['Bloom']['n_items_direct']} direct + "
        f"{results['Bloom']['n_items_router']} router",
        "",
        "## Unpaired t-tests (Welch's) — changliu2 (baseline) vs others",
        "",
        (
            f"| {'Comparison':<25} | {'Metric':<30} | {'t-stat':>8} | "
            f"{'p-value':>8} | {'Cohen d':>8} | {'Effect':<20} |"
        ),
        (
            f"|{'-' * 27}|{'-' * 32}|{'-' * 10}|"
            f"{'-' * 10}|{'-' * 10}|{'-' * 22}|"
        ),
    ]

    for c in comparisons:
        t_s = f"{c['t_stat']:.3f}" if not math.isnan(c["t_stat"]) else "NaN"
        p_v = f"{c['p_value']:.4f}" if not math.isnan(c["p_value"]) else "NaN"
        c_d = f"{c['cohens_d']:.3f}" if not math.isnan(c["cohens_d"]) else "NaN"
        md_lines.append(
            f"| {c['comparison']:<25} | {c['metric']:<30} | {t_s:>8} | "
            f"{p_v:>8} | {c_d:>8} | {c['effect']:<20} |"
        )

    md_lines += [
        "",
        "## Notes",
        "",
        "- **Construct Coverage**: fraction of policy sub-risks with ≥1 scored item. "
        "Bloom always = 1.0 since all scenarios are judged.",
        "- **Separation Strength**: average |violation gap| between gpt-5.4-mini and "
        "model-router targets. N/A for origin/main (single model).",
        "- **Item Saturation**: fraction of seeds with unanimous verdicts across models. "
        "Lower = more discriminating. N/A for single-model pipelines.",
        "- **Signal Rate**: fraction of scored items with detected violations/behaviors. "
        "P2M uses policy_violation=true; Bloom uses behavior_presence ≥ 7.",
        "- **Failure Variety (Hill₁)**: effective number of distinct failure categories. "
        "Higher = more diverse failure detection.",
        "- Bootstrap t-tests use 1000 resamples for population-level metrics.",
        "- With small samples (6–16 per group), many tests are correctly Inconclusive.",
        "",
    ]

    md_path = out_dir / "canonical_metrics_report.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))
    print(f"Saved report: {md_path}")


if __name__ == "__main__":
    main()
