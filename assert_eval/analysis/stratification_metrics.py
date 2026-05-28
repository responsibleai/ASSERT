# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Stratification quality and labeling metrics.

Pure post-hoc statistics computed over test-case assignments and stratification catalogs:
coverage, entropy, effective dimensionality, NMI, confusion matrices, and
inter-rater agreement.
"""

from __future__ import annotations

import math
from collections import Counter
from itertools import combinations
from typing import Any

from assert_eval.core.io import stratification_dimensions, row_behavior

# ---------------------------------------------------------------------------
# Stratification quality
# ---------------------------------------------------------------------------


def normalized_entropy(counts: list[int], level_count: int) -> float:
    if level_count <= 1:
        return 1.0
    total = sum(counts)
    if total <= 0:
        return 0.0
    entropy = 0.0
    for count in counts:
        if count <= 0:
            continue
        p = count / total
        entropy -= p * math.log(p)
    return entropy / math.log(level_count)


def factor_counts(
    assignments: list[dict[str, str]],
    stratification: dict[str, list[dict[str, str]]],
) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for axis in tuple(key for key in stratification if not key.startswith("_")):
        axis_ids = [entry["name"] for entry in stratification[axis]]
        counter = Counter(
            a[axis] for a in assignments if axis in a
        )
        counts[axis] = {aid: counter.get(aid, 0) for aid in axis_ids}
    return counts


def pairwise_cell_coverage(
    assignments: list[dict[str, str]],
    stratification: dict[str, list[dict[str, str]]],
    *,
    axes: tuple[str, ...] | None = None,
) -> dict[str, float]:
    active = axes if axes is not None else tuple(
        key for key in stratification if not key.startswith("_")
    )
    coverage: dict[str, float] = {}
    for axis_a, axis_b in combinations(active, 2):
        possible = len(stratification[axis_a]) * len(stratification[axis_b])
        observed = {
            (a[axis_a], a[axis_b])
            for a in assignments
            if axis_a in a and axis_b in a
        }
        coverage[f"{axis_a}__{axis_b}"] = (
            len(observed) / possible if possible else 0.0
        )
    return coverage


def within_node_coverage_min(
    assignments: list[dict[str, str]],
    stratification: dict[str, list[dict[str, str]]],
) -> float:
    """Worst-case per-dimension entropy across all taxonomy behavior_categories."""
    if "behavior" not in stratification:
        return 0.0
    flattened: list[float] = []
    for policy_behavior in stratification["behavior"]:
        node_assignments = [
            a
            for a in assignments
            if a.get("behavior") == policy_behavior["name"]
        ]
        if not node_assignments:
            return 0.0
        for axis in stratification_dimensions(stratification):
            counts = Counter(
                a[axis] for a in node_assignments if axis in a
            )
            ordered = [
                counts.get(entry["name"], 0) for entry in stratification[axis]
            ]
            flattened.append(normalized_entropy(ordered, len(stratification[axis])))
    return min(flattened) if flattened else 0.0


def coverage_metrics(
    assignments: list[dict[str, str]],
    stratification: dict[str, list[dict[str, str]]],
) -> dict[str, Any]:
    ac = factor_counts(assignments, stratification)
    da = stratification_dimensions(stratification)
    aa = tuple(key for key in stratification if not key.startswith("_"))
    return {
        "factor_counts": ac,
        "per_factor_normalized_entropy": {
            axis: normalized_entropy(
                list(counts.values()), len(stratification[axis])
            )
            for axis, counts in ac.items()
        },
        "stratification_dimensions_pair_cell_coverage": pairwise_cell_coverage(
            assignments, stratification, axes=da
        ),
        "all_factors_pair_cell_coverage": pairwise_cell_coverage(
            assignments, stratification, axes=aa
        ),
        "within_node_coverage_min": within_node_coverage_min(
            assignments, stratification
        ),
    }


def effective_dimensionality(
    assignments: list[dict[str, str]],
    stratification: dict[str, list[dict[str, str]]],
) -> dict[str, Any]:
    import numpy as np

    if len(assignments) < 2:
        return {
            "n_components_90": 0,
            "n_components_95": 0,
            "explained_variance_ratios": [],
        }

    aa = tuple(key for key in stratification if not key.startswith("_"))
    n = len(assignments)
    all_level_ids: list[str] = []
    for axis in aa:
        all_level_ids.extend(entry["name"] for entry in stratification[axis])
    d = len(all_level_ids)

    axis_offsets: dict[str, tuple[int, dict[str, int]]] = {}
    offset = 0
    for axis in aa:
        ids = [entry["name"] for entry in stratification[axis]]
        axis_offsets[axis] = (offset, {lid: i for i, lid in enumerate(ids)})
        offset += len(ids)

    matrix = np.zeros((n, d), dtype=np.float64)
    for i, row in enumerate(assignments):
        for axis in aa:
            base, id_map = axis_offsets[axis]
            idx = id_map.get(row.get(axis, ""), -1)
            if idx >= 0:
                matrix[i, base + idx] = 1.0

    matrix -= matrix.mean(axis=0)
    _, s, _ = np.linalg.svd(matrix, full_matrices=False)
    var_explained = s**2
    total_var = var_explained.sum()
    if total_var == 0:
        return {
            "n_components_90": 0,
            "n_components_95": 0,
            "explained_variance_ratios": [],
        }
    ratios = (var_explained / total_var).tolist()
    cumulative = 0.0
    n90, n95 = len(ratios), len(ratios)
    for i, r in enumerate(ratios):
        cumulative += r
        if cumulative >= 0.90 and n90 == len(ratios):
            n90 = i + 1
        if cumulative >= 0.95:
            n95 = i + 1
            break
    return {
        "n_components_90": n90,
        "n_components_95": n95,
        "explained_variance_ratios": [
            round(r, 6) for r in ratios[: n95 + 3]
        ],
    }


def cross_axis_nmi(
    assignments: list[dict[str, str]],
    stratification: dict[str, list[dict[str, str]]],
) -> dict[str, float]:
    if len(assignments) < 2:
        return {}
    aa = tuple(key for key in stratification if not key.startswith("_"))
    n = len(assignments)
    nmi_map: dict[str, float] = {}
    for axis_a, axis_b in combinations(aa, 2):
        joint: dict[tuple[str, str], int] = Counter()
        marginal_a: dict[str, int] = Counter()
        marginal_b: dict[str, int] = Counter()
        for row in assignments:
            a_val = row.get(axis_a, "")
            b_val = row.get(axis_b, "")
            joint[(a_val, b_val)] += 1
            marginal_a[a_val] += 1
            marginal_b[b_val] += 1
        mi = 0.0
        for (a_val, b_val), count in joint.items():
            p_ab = count / n
            p_a = marginal_a[a_val] / n
            p_b = marginal_b[b_val] / n
            if p_ab > 0 and p_a > 0 and p_b > 0:
                mi += p_ab * math.log(p_ab / (p_a * p_b))
        h_a = -sum(
            (c / n) * math.log(c / n) for c in marginal_a.values() if c > 0
        )
        h_b = -sum(
            (c / n) * math.log(c / n) for c in marginal_b.values() if c > 0
        )
        denom = min(h_a, h_b)
        nmi_map[f"{axis_a}__{axis_b}"] = (
            round(mi / denom, 6) if denom > 0 else 0.0
        )
    return nmi_map


# ---------------------------------------------------------------------------
# Labeling quality
# ---------------------------------------------------------------------------


def intended_vs_observed_metrics(
    intended: list[dict[str, str]],
    observed: list[dict[str, str]],
    stratification: dict[str, list[dict[str, str]]],
) -> dict[str, Any]:
    aa = tuple(key for key in stratification if not key.startswith("_"))
    intended_by_id = {a["test_case_id"]: a for a in intended}
    observed_by_id = {a["test_case_id"]: a for a in observed}
    shared = sorted(set(intended_by_id) & set(observed_by_id))
    if not shared:
        return {
            "per_axis_agreement": {a: 0.0 for a in aa},
            "exact_tuple_agreement": 0.0,
        }
    per_axis: dict[str, float] = {}
    for axis in aa:
        matches = sum(
            1
            for sid in shared
            if intended_by_id[sid][axis] == observed_by_id[sid][axis]
        )
        per_axis[axis] = matches / len(shared)
    exact = sum(
        1
        for sid in shared
        if all(
            intended_by_id[sid][a] == observed_by_id[sid][a] for a in aa
        )
    )
    return {
        "per_axis_agreement": per_axis,
        "exact_tuple_agreement": exact / len(shared),
    }


def confusion_matrices(
    intended: list[dict[str, str]],
    observed: list[dict[str, str]],
    stratification: dict[str, list[dict[str, str]]],
) -> dict[str, dict[str, dict[str, int]]]:
    intended_by_id = {a["test_case_id"]: a for a in intended}
    observed_by_id = {a["test_case_id"]: a for a in observed}
    shared = sorted(set(intended_by_id) & set(observed_by_id))
    if not shared:
        return {}
    matrices: dict[str, dict[str, dict[str, int]]] = {}
    for axis in tuple(key for key in stratification if not key.startswith("_")):
        level_ids = [entry["name"] for entry in stratification[axis]]
        matrix: dict[str, dict[str, int]] = {
            i_id: {o_id: 0 for o_id in level_ids} for i_id in level_ids
        }
        for sid in shared:
            i_val = intended_by_id[sid].get(axis, "")
            o_val = observed_by_id[sid].get(axis, "")
            if i_val in matrix and o_val in matrix[i_val]:
                matrix[i_val][o_val] += 1
        matrices[axis] = matrix
    return matrices


def labeler_retest_agreement(
    labels_a: list[dict[str, str]],
    labels_b: list[dict[str, str]],
    stratification: dict[str, list[dict[str, str]]],
) -> dict[str, float]:
    aa = tuple(key for key in stratification if not key.startswith("_"))
    a_by_id = {r["test_case_id"]: r for r in labels_a}
    b_by_id = {r["test_case_id"]: r for r in labels_b}
    shared = sorted(set(a_by_id) & set(b_by_id))
    if not shared:
        return {axis: 0.0 for axis in aa}
    return {
        axis: sum(
            1
            for sid in shared
            if a_by_id[sid].get(axis) == b_by_id[sid].get(axis)
        )
        / len(shared)
        for axis in aa
    }


def behavior_agreement(
    observed_assignments: list[dict[str, str]],
    rows: list[dict[str, Any]],
) -> float:
    """Agreement between labeler-assigned behavior and conditioning behavior."""
    if not observed_assignments or not rows:
        return 0.0
    if not any("behavior" in a for a in observed_assignments):
        return 0.0
    observed_by_test_case_id = {
        a["test_case_id"]: a["behavior"] for a in observed_assignments
        if "behavior" in a
    }
    total = 0
    matches = 0
    for row in rows:
        test_case_id = str(row.get("test_case_id") or "")
        if test_case_id not in observed_by_test_case_id:
            continue
        total += 1
        if observed_by_test_case_id[test_case_id] == row_behavior(row):
            matches += 1
    return matches / total if total else 0.0


def build_supplementary_metrics(
    *,
    kind: str,
    stratification: dict[str, list[dict[str, str]]],
    rows: list[dict[str, Any]],
    observed_assignments: list[dict[str, str]],
    intended_assignments: list[dict[str, str]] | None,
    retest_assignments: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    stratification_quality: dict[str, Any] = coverage_metrics(
        observed_assignments, stratification
    )
    if kind == "scenario":
        stratification_quality["effective_dimensionality"] = effective_dimensionality(
            observed_assignments, stratification
        )
        stratification_quality["cross_axis_nmi"] = cross_axis_nmi(observed_assignments, stratification)

    labeling_quality: dict[str, Any] = {}
    if "behavior" in stratification:
        labeling_quality["behavior_agreement_with_test_case_behavior"] = behavior_agreement(
            observed_assignments, rows
        )
    if intended_assignments is not None:
        labeling_quality["intended_vs_observed"] = intended_vs_observed_metrics(
            intended_assignments, observed_assignments, stratification
        )
        labeling_quality["confusion_matrices"] = confusion_matrices(
            intended_assignments, observed_assignments, stratification
        )
    if retest_assignments is not None:
        labeling_quality["labeler_retest_agreement"] = labeler_retest_agreement(
            observed_assignments, retest_assignments, stratification
        )

    return {
        "type": kind,
        "stratification_quality": stratification_quality,
        "labeling_quality": labeling_quality,
    }
