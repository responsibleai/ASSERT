"""Concrete stage modules for the p2m pipeline."""

from __future__ import annotations

from . import design, judge, policy, rollout, seeds

STAGES = {
    "policy": policy,
    "design": design,
    "seeds": seeds,
    "rollout": rollout,
    "judge": judge,
}

STAGE_NAMES = tuple(STAGES)

__all__ = ["STAGES", "STAGE_NAMES"]
