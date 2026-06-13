# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Round-trip validate a generated ACS policy against ASSERT's known-bad findings.

A generated policy is only trustworthy if it actually reacts to the failures
ASSERT observed. This module replays each known-bad :class:`FailingExample`
through the native ACS runtime and reports whether the policy declined to
``allow`` it via a genuine rule (``deny``/``escalate``/``warn``/``transform``).

Two outcomes are deliberately NOT counted as handled, because the deployed
runtime guard would not actually protect them:

* an example whose intervention point the manifest does not declare (the
  manifest-aware guard skips undeclared points, and the stateless runtime fails
  closed to ``deny`` on them), and
* a verdict whose ``reason`` is a ``runtime_error:`` (the runtime failed closed
  rather than a policy rule matching the content).

Requires the optional ``agent-control-specification`` SDK
(``pip install "assert-ai[acs]"``).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from agent_control_specification import (
        InterventionPoint,
        InterventionPointRequest,
        NativeRuntimeClient,
    )
except ModuleNotFoundError as exc:  # pragma: no cover - exercised only without the extra
    if exc.name == "agent_control_specification":
        raise ModuleNotFoundError(
            "ACS policy validation requires the 'agent-control-specification' SDK. "
            'Install the ACS extra with: pip install "assert-ai[acs]"',
            name=exc.name,
        ) from exc
    raise

from assert_ai.integrations.acs.findings import FailingExample, FindingsSummary

# Decisions that mean the policy did NOT pass the known-bad action through
# untouched. ``warn`` still permits execution but flags the action, so we treat
# it as a (soft) reaction; ``deny`` and ``escalate`` halt the action.
_HANDLED_DECISIONS = frozenset({"deny", "escalate", "warn", "transform"})
_STRONG_BLOCK_DECISIONS = frozenset({"deny", "escalate"})

# The stateless runtime fails closed to ``deny`` on a runtime error (e.g. an
# undeclared intervention point or invalid policy output). Such a verdict is NOT
# a policy rule reacting to the content, so it must not count as handled.
_RUNTIME_ERROR_PREFIX = "runtime_error:"

# Reserved reason returned when the example targets a point the effective policy
# does not declare. Detecting it via the runtime (which resolves the full
# ``extends`` chain) is more correct than parsing one manifest file's points.
# Source: policy-engine core/src/error.rs (InterventionPointUnknown).
_UNKNOWN_POINT_REASON = "runtime_error:intervention_point_unknown"

# Synthetic decision label for an example whose intervention point the effective
# policy does not declare; reported as an unguarded coverage gap.
_UNGUARDED_DECISION = "unguarded"


@dataclass(frozen=True)
class ValidationCase:
    """Outcome of replaying one known-bad example through the policy."""

    intervention_point: str
    behavior: str
    dimension: str
    target_value: str
    decision: str
    handled: bool
    strong_block: bool
    reason: str | None


@dataclass(frozen=True)
class ValidationReport:
    """Aggregate result of validating a policy against ASSERT findings."""

    manifest_path: Path
    total: int
    handled: int
    strong_blocked: int
    cases: tuple[ValidationCase, ...]

    @property
    def failed(self) -> int:
        """Known-bad examples the policy still allowed (no reaction at all)."""
        return self.total - self.handled

    @property
    def not_blocked(self) -> int:
        """Known-bad examples the policy did not strongly block (deny/escalate).

        Includes examples that were merely ``warn``-ed (which still permit
        execution at runtime) as well as allowed and unguarded ones.
        """
        return self.total - self.strong_blocked

    @property
    def ok(self) -> bool:
        """True when every known-bad example was handled (none allowed).

        Note ``warn`` counts as handled because the policy reacted, even though a
        warn still permits execution. Use :attr:`fully_blocked` for a strict
        "every known-bad example is actually blocked" gate. Vacuously true when
        there are no known-bad examples to replay.
        """
        return self.failed == 0

    @property
    def fully_blocked(self) -> bool:
        """True when every known-bad example was strongly blocked (deny/escalate).

        This is the strict gate that matches "block the violation at runtime": a
        policy that only ``warn``-s on a known-bad example is NOT fully blocked.
        Vacuously true when there are no known-bad examples to replay.
        """
        return self.not_blocked == 0

    @property
    def handled_rate(self) -> float:
        return self.handled / self.total if self.total else 1.0

    @property
    def strong_block_rate(self) -> float:
        return self.strong_blocked / self.total if self.total else 1.0


def validate_policy(
    manifest_path: str | Path,
    findings: FindingsSummary,
    *,
    max_cases: int | None = None,
) -> ValidationReport:
    """Replay ASSERT's known-bad examples through the policy at ``manifest_path``.

    Synchronous wrapper over :func:`validate_policy_async`.
    """
    return asyncio.run(
        validate_policy_async(manifest_path, findings, max_cases=max_cases)
    )


async def validate_policy_async(
    manifest_path: str | Path,
    findings: FindingsSummary,
    *,
    max_cases: int | None = None,
) -> ValidationReport:
    """Async implementation of :func:`validate_policy`."""
    resolved = Path(manifest_path).expanduser()
    if not resolved.is_file():
        raise FileNotFoundError(f"ACS manifest not found: {resolved}")

    examples = list(findings.failing_examples)
    if max_cases is not None:
        examples = examples[:max_cases]

    client = NativeRuntimeClient.from_path(str(resolved))

    cases: list[ValidationCase] = []
    for example in examples:
        point = _intervention_point(InterventionPoint, example.intervention_point)
        request = InterventionPointRequest(
            intervention_point=point,
            snapshot=dict(example.snapshot),
        )
        result = await client.evaluate_intervention_point(request)
        cases.append(_build_case(example, result))

    handled = sum(1 for case in cases if case.handled)
    strong = sum(1 for case in cases if case.strong_block)
    return ValidationReport(
        manifest_path=resolved,
        total=len(cases),
        handled=handled,
        strong_blocked=strong,
        cases=tuple(cases),
    )


def _build_case(example: FailingExample, result: Any) -> ValidationCase:
    decision = _decision_value(result.verdict.decision)
    reason = result.verdict.reason
    # An example targeting a point the effective policy does not declare is an
    # unguarded coverage gap, not a block (the runtime guard skips it). Detect it
    # via the reserved unknown-point reason so ``extends``-inherited points still
    # validate normally.
    if isinstance(reason, str) and reason == _UNKNOWN_POINT_REASON:
        return ValidationCase(
            intervention_point=example.intervention_point,
            behavior=example.behavior,
            dimension=example.dimension,
            target_value=example.target_value,
            decision=_UNGUARDED_DECISION,
            handled=False,
            strong_block=False,
            reason=(
                f"intervention point '{example.intervention_point}' is not declared "
                "by the policy; the runtime guard does not enforce it"
            ),
        )
    # Any other runtime-error deny is the runtime failing closed, not a policy
    # rule reacting to the content, so it does not count as the policy handling it.
    is_runtime_error = isinstance(reason, str) and reason.startswith(_RUNTIME_ERROR_PREFIX)
    return ValidationCase(
        intervention_point=example.intervention_point,
        behavior=example.behavior,
        dimension=example.dimension,
        target_value=example.target_value,
        decision=decision,
        handled=decision in _HANDLED_DECISIONS and not is_runtime_error,
        strong_block=decision in _STRONG_BLOCK_DECISIONS and not is_runtime_error,
        reason=reason,
    )


def _intervention_point(enum_cls: Any, value: str) -> Any:
    """Coerce a stored intervention-point string into the SDK enum, else pass through."""
    try:
        return enum_cls(value)
    except (ValueError, KeyError):
        return value


def _decision_value(decision: Any) -> str:
    """Normalize a Decision enum (or string) to its lower-case wire value."""
    value = getattr(decision, "value", decision)
    return str(value).lower()


__all__ = [
    "ValidationCase",
    "ValidationReport",
    "validate_policy",
    "validate_policy_async",
]
