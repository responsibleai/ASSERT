"""Core statistical utilities for pipeline analysis.

Provides cluster-bootstrapped confidence intervals, binary prediction metrics,
and group-level aggregation helpers. All functions accept plain dicts/lists
and return plain dicts — no dataframe dependency.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

import numpy as np

# Standard normal quantiles for common confidence levels.
# Hardcoded because scipy is not a dependency; these cover all supported alpha values.
_Z_SCORES = {0.10: 1.645, 0.05: 1.96, 0.01: 2.576}


def _wilson_ci(k: int, n: int, alpha: float = 0.10) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion."""
    if n == 0:
        return (0.0, 0.0)
    z = _Z_SCORES.get(alpha, 1.645)
    p_hat = k / n
    denom = 1 + z * z / n
    center = (p_hat + z * z / (2 * n)) / denom
    spread = z * math.sqrt((p_hat * (1 - p_hat) + z * z / (4 * n)) / n) / denom
    return (max(0.0, center - spread), min(1.0, center + spread))


def binary_rate_ci(
    outcomes: list[bool],
    *,
    groups: list[str] | None = None,
    n_boot: int = 2000,
    alpha: float = 0.10,
    seed: int = 42,
) -> dict[str, Any]:
    """Compute a rate with confidence interval.

    For single-run rates (no groups, or cluster size = 1), uses Wilson
    score interval. For multi-observation clusters, uses cluster bootstrap.

    Returns {rate, ci_lower, ci_upper, n, n_positive}.
    """
    n = len(outcomes)
    if n == 0:
        return {"rate": None, "ci_lower": None, "ci_upper": None, "n": 0, "n_positive": 0}

    n_positive = sum(outcomes)
    observed_rate = n_positive / n

    # Use Wilson CI when there are no multi-observation clusters
    if groups is None or n_boot <= 0:
        ci_lo, ci_hi = _wilson_ci(n_positive, n, alpha)
        return {
            "rate": observed_rate,
            "ci_lower": ci_lo,
            "ci_upper": ci_hi,
            "n": n,
            "n_positive": n_positive,
        }

    cluster_sizes: dict[str, int] = defaultdict(int)
    for g in groups:
        cluster_sizes[g] += 1
    if not any(s > 1 for s in cluster_sizes.values()):
        ci_lo, ci_hi = _wilson_ci(n_positive, n, alpha)
        return {
            "rate": observed_rate,
            "ci_lower": ci_lo,
            "ci_upper": ci_hi,
            "n": n,
            "n_positive": n_positive,
        }

    # Cluster bootstrap for multi-observation clusters
    arr = np.array(outcomes, dtype=float)

    cluster_map: dict[str, list[int]] = defaultdict(list)
    for i, g in enumerate(groups):
        cluster_map[g].append(i)
    cluster_keys = sorted(cluster_map.keys())
    cluster_indices = [np.array(cluster_map[k]) for k in cluster_keys]
    n_clusters = len(cluster_keys)

    rng = np.random.RandomState(seed)
    boot_rates = np.empty(n_boot)

    for b in range(n_boot):
        sampled = rng.choice(n_clusters, size=n_clusters, replace=True)
        indices = np.concatenate([cluster_indices[i] for i in sampled])
        boot_rates[b] = arr[indices].mean()

    ci_lower = float(np.percentile(boot_rates, 100 * alpha / 2))
    ci_upper = float(np.percentile(boot_rates, 100 * (1 - alpha / 2)))

    # If bootstrap gives degenerate CI, fall back to Wilson
    if ci_lower == ci_upper == observed_rate:
        ci_lower, ci_upper = _wilson_ci(n_positive, n, alpha)

    return {
        "rate": observed_rate,
        "ci_lower": ci_lower,
        "ci_upper": ci_upper,
        "n": n,
        "n_positive": n_positive,
    }


def macro_rate(
    per_group: dict[str, dict[str, Any]],
    *,
    rate_key: str = "rate",
    min_support: int = 1,
) -> dict[str, Any]:
    """Compute macro-average rate across groups.

    Returns {rate, n_groups, n_groups_included, groups_excluded}.
    Groups with fewer than min_support items are excluded.
    """
    rates = []
    excluded = []
    for g, stats in per_group.items():
        r = stats.get(rate_key)
        count = stats.get("count", 0)
        if r is None or count < min_support:
            excluded.append(g)
            continue
        rates.append(r)

    if not rates:
        return {
            "rate": None,
            "n_groups": len(per_group),
            "n_groups_included": 0,
            "groups_excluded": excluded,
        }

    return {
        "rate": sum(rates) / len(rates),
        "n_groups": len(per_group),
        "n_groups_included": len(rates),
        "groups_excluded": excluded,
    }
