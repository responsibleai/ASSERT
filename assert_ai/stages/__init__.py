# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Concrete stage modules for the ASSERT pipeline."""

from __future__ import annotations

from . import judge, inference, red_team, systematize, test_set

STAGES = {
    "systematize": systematize,
    "test_set": test_set,
    "inference": inference,
    "red_team": red_team,
    "judge": judge,
}

STAGE_NAMES = tuple(STAGES)

__all__ = ["STAGES", "STAGE_NAMES"]
