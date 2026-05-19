"""Concrete stage modules for the p2m pipeline."""

from __future__ import annotations

from . import judge, inference, systematize, test_set

STAGES = {
    "systematize": systematize,
    "test_set": test_set,
    "inference": inference,
    "judge": judge,
}

STAGE_NAMES = tuple(STAGES)

__all__ = ["STAGES", "STAGE_NAMES"]
