# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Adapter that turns ASSERT evaluation findings into deployable ACS policies.

ASSERT discovers *how* an agent fails (per-behavior and per-dimension violation
rates in ``scores.jsonl``, the failing behavior categories in ``node_judgments``,
and taxonomy permissibility in ``taxonomy.json``). The Agent Governance Toolkit
ships ACS (Agent Control Specification), a stateless intervention-point policy
runtime that secures an agent at runtime. This subpackage bridges the two: it
summarizes a completed ASSERT run, synthesizes a natural-language guardrail
prompt plus a tool inventory, hands them to AGT's ``acs-generator`` to produce a
deployable ``manifest.yaml`` + Rego policy, validates that policy by
round-tripping known-bad findings through the ACS Python SDK, and exposes a
runtime guard so the same agent target can be re-run secured.

Layered dependencies
--------------------
``findings`` and ``prompt_builder`` depend only on ``assert_ai`` and the standard
library, so they import eagerly and are usable without the optional ACS extra.
``generate`` requires ``acs-generator`` and ``validate``/``guard`` require the
native ``agent-control-specification`` SDK; those symbols load lazily and raise a
clear install hint (``pip install "assert-ai[acs]"``) when the extra is absent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import-time typing only
    from assert_ai.integrations.acs.findings import (
        BehaviorFinding,
        DimensionFinding,
        FailingExample,
        FindingsSummary,
        load_findings,
        summarize_findings,
    )
    from assert_ai.integrations.acs.generate import PolicyArtifacts, generate_policy
    from assert_ai.integrations.acs.guard import (
        build_agent_control,
        guard_target,
        manifest_intervention_points,
    )
    from assert_ai.integrations.acs.language_model import (
        AssertLanguageModel,
        build_language_model,
    )
    from assert_ai.integrations.acs.prompt_builder import (
        GuardrailPrompt,
        build_guardrail_prompt,
    )
    from assert_ai.integrations.acs.validate import (
        ValidationCase,
        ValidationReport,
        validate_policy,
        validate_policy_async,
    )

__all__ = [
    "BehaviorFinding",
    "DimensionFinding",
    "FailingExample",
    "FindingsSummary",
    "load_findings",
    "summarize_findings",
    "GuardrailPrompt",
    "build_guardrail_prompt",
    "AssertLanguageModel",
    "build_language_model",
    "PolicyArtifacts",
    "generate_policy",
    "ValidationCase",
    "ValidationReport",
    "validate_policy",
    "validate_policy_async",
    "build_agent_control",
    "guard_target",
    "manifest_intervention_points",
]

# Map each lazily-loaded public symbol to the submodule that defines it. The
# submodule import is what triggers the optional third-party dependency, so we
# defer it until the symbol is actually requested.
_LAZY_EXPORTS = {
    "BehaviorFinding": "findings",
    "DimensionFinding": "findings",
    "FailingExample": "findings",
    "FindingsSummary": "findings",
    "load_findings": "findings",
    "summarize_findings": "findings",
    "GuardrailPrompt": "prompt_builder",
    "build_guardrail_prompt": "prompt_builder",
    "AssertLanguageModel": "language_model",
    "build_language_model": "language_model",
    "PolicyArtifacts": "generate",
    "generate_policy": "generate",
    "ValidationCase": "validate",
    "ValidationReport": "validate",
    "validate_policy": "validate",
    "validate_policy_async": "validate",
    "build_agent_control": "guard",
    "guard_target": "guard",
    "manifest_intervention_points": "guard",
}

# Human-readable install hints per submodule, surfaced when the optional
# dependency that backs a lazy symbol is missing.
_MISSING_DEPENDENCY_HINT = {
    "generate": (
        "The ACS policy generator requires the 'acs-generator' package. "
        'Install the ACS extra with: pip install "assert-ai[acs]"'
    ),
    "validate": (
        "ACS policy validation requires the 'agent-control-specification' SDK. "
        'Install the ACS extra with: pip install "assert-ai[acs]"'
    ),
    "guard": (
        "The ACS runtime guard requires the 'agent-control-specification' SDK. "
        'Install the ACS extra with: pip install "assert-ai[acs]"'
    ),
}


def __getattr__(name: str) -> object:
    """Lazily import ACS-backed symbols (PEP 562).

    Keeping these imports lazy means ``import assert_ai.integrations.acs`` and the
    pure findings/prompt helpers work even when the optional ACS packages are not
    installed. The optional dependency is only required when a caller actually
    reaches for ``generate_policy``, ``validate_policy``, or ``guard_target``.
    """
    submodule = _LAZY_EXPORTS.get(name)
    if submodule is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    try:
        module = importlib.import_module(f"{__name__}.{submodule}")
    except ModuleNotFoundError as exc:
        hint = _MISSING_DEPENDENCY_HINT.get(submodule)
        if hint is None:
            raise
        raise ModuleNotFoundError(hint, name=exc.name) from exc
    return getattr(module, name)


def __dir__() -> list[str]:
    return sorted(__all__)
