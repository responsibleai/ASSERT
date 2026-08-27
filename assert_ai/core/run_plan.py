# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Pure stage-selection helpers shared by preflight and execution."""

from __future__ import annotations

from collections.abc import Iterable

from assert_ai.core.config_document import PIPELINE_STAGE_ORDER


def resolve_forced_stages(
    configured_stage_names: Iterable[str],
    requested_force_stages: Iterable[str],
) -> tuple[str, ...]:
    """Validate forced stages and apply the runner's downstream cascade."""
    configured = set(configured_stage_names)
    requested = set(requested_force_stages)
    invalid = sorted(requested.difference(configured))
    if invalid:
        raise ValueError(
            "Forced stage(s) not present in config: "
            + ", ".join(invalid)
        )

    forced = set(requested)
    forced_indices = [
        PIPELINE_STAGE_ORDER.index(name)
        for name in requested
        if name in PIPELINE_STAGE_ORDER
    ]
    if forced_indices:
        first_forced = min(forced_indices)
        forced.update(
            name
            for name in PIPELINE_STAGE_ORDER[first_forced:]
            if name in configured
        )
    return tuple(
        name for name in PIPELINE_STAGE_ORDER if name in forced
    )
