"""Concrete stage modules for the p2m pipeline."""

from __future__ import annotations

from . import design, judge, taxonomy, inference, seeds, systematization, systematization_convert

STAGES = {
    "taxonomy": taxonomy,
    "design": design,
    "seeds": seeds,
    "inference": inference,
    "judge": judge,
    "systematization": systematization,
    "systematization_convert": systematization_convert,
}

STAGE_NAMES = tuple(STAGES)

__all__ = ["STAGES", "STAGE_NAMES"]
