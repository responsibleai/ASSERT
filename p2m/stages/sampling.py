"""Seed sampling strategies.

Three named methods select assignments over the cross-product of design axes:

- ``stratified`` (default): equal allocation per stratum (one or more axes);
  within each stratum, the remaining axes are sampled IID with replacement.
  When the budget is smaller than the number of strata, falls back to
  stratum subsampling — picks ``sample_size`` strata uniformly at random and
  gives each one seed.
- ``full_factorial``: every cell of the cross-product at least once, with
  optional balanced replication if the budget exceeds the cell count.
- ``random``: independent uniform draws over the full cross-product.

Each method's parameters live flat under the ``sampling`` config block.
Validation runs against the design at sample time so that errors name the
exact axis or capacity that failed.

The sampler treats every entry of ``design`` as a regular axis, including
``behavior``. The seeds stage is responsible for injecting policy behaviors
into the design before calling :func:`sample_assignments`.
"""

from __future__ import annotations

import random
from itertools import product
from typing import Any

_ALLOWED_KEYS_BY_METHOD: dict[str, set[str]] = {
    "stratified": {"method", "stratify_by"},
    "full_factorial": {"method", "replication"},
    "random": {"method", "with_replacement"},
}

_ALLOWED_REPLICATION = {"balanced", "none"}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def sample_assignments(
    *,
    design: dict[str, list[dict[str, Any]]],
    sample_size: int,
    sampling: dict[str, Any] | None,
    rng: random.Random,
) -> list[dict[str, str]]:
    """Return a list of axis assignments selected by the configured method.

    Each assignment is a flat ``{axis_name: level_name}`` dict covering every
    axis in ``design`` (other than internal ``_``-prefixed metadata).
    """
    if sample_size <= 0:
        return []
    axes = _design_axes(design)
    if not axes:
        raise ValueError("sampling requires at least one design axis")
    _validate_design_levels(design, axes)

    cfg = resolve_sampling_config(sampling, design=design)
    method = cfg["method"]
    if method == "stratified":
        return _stratified(
            design=design,
            sample_size=sample_size,
            axes=axes,
            stratify_by=cfg["stratify_by"],
            rng=rng,
        )
    if method == "full_factorial":
        return _full_factorial(
            design=design,
            sample_size=sample_size,
            axes=axes,
            replication=cfg["replication"],
            rng=rng,
        )
    if method == "random":
        return _random(
            design=design,
            sample_size=sample_size,
            axes=axes,
            with_replacement=cfg["with_replacement"],
            rng=rng,
        )
    raise ValueError(f"unknown sampling method: {method!r}")


# ---------------------------------------------------------------------------
# Config resolution / validation
# ---------------------------------------------------------------------------


def validate_sampling_shape(sampling: dict[str, Any] | None) -> dict[str, Any]:
    """Type/shape-check ``sampling`` without consulting any design.

    Returns the normalized config. Safe to call at config-parse time so that
    typos surface with the ``seeds.{kind}.sampling`` path before any I/O.
    Design-dependent checks (e.g. ``stratify_by`` axes existing) run later
    via :func:`resolve_sampling_config`.
    """
    if sampling is None:
        sampling = {"method": "stratified"}
    if not isinstance(sampling, dict):
        raise ValueError("sampling must be a mapping")

    method = sampling.get("method")
    if not isinstance(method, str):
        raise ValueError(
            f"sampling.method must be a string; got {type(method).__name__}"
        )
    if method not in _ALLOWED_KEYS_BY_METHOD:
        allowed = sorted(_ALLOWED_KEYS_BY_METHOD)
        raise ValueError(f"sampling.method must be one of {allowed}; got {method!r}")
    extra = set(sampling) - _ALLOWED_KEYS_BY_METHOD[method]
    if extra:
        raise ValueError(
            f"sampling[method={method!r}] received unknown keys: {sorted(extra)}"
        )

    if method == "stratified":
        stratify_by = sampling.get("stratify_by", ["behavior"])
        if not isinstance(stratify_by, list) or not stratify_by:
            raise ValueError("sampling.stratify_by must be a non-empty list")
        if not all(isinstance(ax, str) for ax in stratify_by):
            raise ValueError("sampling.stratify_by items must be strings")
        if len(set(stratify_by)) != len(stratify_by):
            raise ValueError("sampling.stratify_by must not contain duplicates")
        return {
            "method": "stratified",
            "stratify_by": list(stratify_by),
        }
    if method == "full_factorial":
        replication = sampling.get("replication", "balanced")
        if not isinstance(replication, str) or replication not in _ALLOWED_REPLICATION:
            raise ValueError(
                f"sampling.replication must be one of {sorted(_ALLOWED_REPLICATION)}; "
                f"got {replication!r}"
            )
        return {"method": "full_factorial", "replication": replication}
    # method == "random"
    with_replacement = sampling.get("with_replacement", True)
    if not isinstance(with_replacement, bool):
        raise ValueError(
            f"sampling.with_replacement must be a bool; "
            f"got {type(with_replacement).__name__}"
        )
    return {"method": "random", "with_replacement": with_replacement}


def resolve_sampling_config(
    sampling: dict[str, Any] | None,
    *,
    design: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Validate ``sampling`` shape and check it against the actual ``design``."""
    cfg = validate_sampling_shape(sampling)
    if cfg["method"] == "stratified":
        axis_set = set(_design_axes(design))
        for ax in cfg["stratify_by"]:
            if ax not in axis_set:
                raise ValueError(
                    f"sampling.stratify_by axis {ax!r} not in design axes "
                    f"{sorted(axis_set)}"
                )
    return cfg


# ---------------------------------------------------------------------------
# Method implementations
# ---------------------------------------------------------------------------


def _stratified(
    *,
    design: dict[str, list[dict[str, Any]]],
    sample_size: int,
    axes: tuple[str, ...],
    stratify_by: list[str],
    rng: random.Random,
) -> list[dict[str, str]]:
    levels = _design_levels(design, axes)
    stratum_axes = tuple(stratify_by)
    stratum_combinations = [
        dict(zip(stratum_axes, values, strict=True))
        for values in product(*(levels[ax] for ax in stratum_axes))
    ]
    n_strata = len(stratum_combinations)

    if sample_size < n_strata:
        # Budget is too small for one-per-stratum balance. Degrade to
        # stratum subsampling: pick ``sample_size`` strata uniformly at
        # random and give each one seed. Per-stratum estimates exist only
        # for the strata that were selected.
        allocations = [0] * n_strata
        for i in rng.sample(range(n_strata), sample_size):
            allocations[i] = 1
    else:
        base, remainder = divmod(sample_size, n_strata)
        allocations = [base] * n_strata
        for i in rng.sample(range(n_strata), remainder):
            allocations[i] += 1

    stratify_set = set(stratify_by)
    inner_axes = tuple(ax for ax in axes if ax not in stratify_set)
    sub_design = {ax: design[ax] for ax in inner_axes}

    out: list[dict[str, str]] = []
    for stratum_assignment, alloc in zip(stratum_combinations, allocations, strict=True):
        if alloc == 0:
            continue
        if not inner_axes:
            out.extend(dict(stratum_assignment) for _ in range(alloc))
            continue
        inner_rows = _random(
            design=sub_design,
            sample_size=alloc,
            axes=inner_axes,
            with_replacement=True,
            rng=rng,
        )
        for row in inner_rows:
            assignment = dict(stratum_assignment)
            assignment.update(row)
            out.append(assignment)

    rng.shuffle(out)
    return out


def _full_factorial(
    *,
    design: dict[str, list[dict[str, Any]]],
    sample_size: int,
    axes: tuple[str, ...],
    replication: str,
    rng: random.Random,
) -> list[dict[str, str]]:
    levels = _design_levels(design, axes)
    full = [
        dict(zip(axes, values, strict=True))
        for values in product(*(levels[ax] for ax in axes))
    ]
    full_size = len(full)

    if replication == "none":
        if sample_size != full_size:
            raise ValueError(
                f"sampling.method=full_factorial with replication=none requires "
                f"sample_size ({sample_size}) == full factorial size ({full_size})"
            )
        rng.shuffle(full)
        return full

    if sample_size < full_size:
        raise ValueError(
            f"sampling.method=full_factorial with replication=balanced requires "
            f"sample_size ({sample_size}) >= full factorial size ({full_size}); "
            f"factorial covers every cell at least once. Use method=stratified or "
            f"method=random for smaller budgets."
        )

    base, remainder = divmod(sample_size, full_size)
    counts = [base] * full_size
    for i in rng.sample(range(full_size), remainder):
        counts[i] += 1
    out: list[dict[str, str]] = []
    for cell, count in zip(full, counts, strict=True):
        out.extend([dict(cell) for _ in range(count)])
    rng.shuffle(out)
    return out


def _random(
    *,
    design: dict[str, list[dict[str, Any]]],
    sample_size: int,
    axes: tuple[str, ...],
    with_replacement: bool,
    rng: random.Random,
) -> list[dict[str, str]]:
    levels = _design_levels(design, axes)
    if with_replacement:
        return [
            {ax: rng.choice(levels[ax]) for ax in axes}
            for _ in range(sample_size)
        ]
    full_size = 1
    for ax in axes:
        full_size *= len(levels[ax])
    if sample_size > full_size:
        raise ValueError(
            f"sampling.method=random with with_replacement=false requires "
            f"sample_size ({sample_size}) <= full factorial size ({full_size})"
        )
    full = [
        dict(zip(axes, values, strict=True))
        for values in product(*(levels[ax] for ax in axes))
    ]
    rng.shuffle(full)
    return full[:sample_size]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _design_axes(design: dict[str, Any]) -> tuple[str, ...]:
    return tuple(key for key in design if not key.startswith("_"))


def _validate_design_levels(
    design: dict[str, list[dict[str, Any]]], axes: tuple[str, ...]
) -> None:
    """Ensure each axis maps to a non-empty list of well-formed level dicts."""
    for ax in axes:
        levels = design.get(ax)
        if not isinstance(levels, list) or not levels:
            raise ValueError(f"design axis {ax!r} must contain at least one level")
        for i, entry in enumerate(levels):
            if not isinstance(entry, dict):
                raise ValueError(
                    f"design axis {ax!r} level {i} must be a mapping; "
                    f"got {type(entry).__name__}"
                )
            name = entry.get("name")
            if not isinstance(name, str) or not name:
                raise ValueError(
                    f"design axis {ax!r} level {i} is missing a non-empty 'name'"
                )


def _design_levels(
    design: dict[str, list[dict[str, Any]]], axes: tuple[str, ...]
) -> dict[str, list[str]]:
    return {ax: [entry["name"] for entry in design[ax]] for ax in axes}
