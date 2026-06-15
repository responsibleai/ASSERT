# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Generate deployable ACS policies from ASSERT findings.

This module is the glue between ASSERT's findings summary and AGT's
``acs-generator``. It synthesizes a natural-language guardrail prompt plus a tool
inventory from a :class:`~assert_ai.integrations.acs.findings.FindingsSummary`,
hands them to ``acs_generator.GenerationEngine``, and returns the written policy
artifacts (``manifest.yaml`` + Rego bundle + ``report.md``).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

try:
    from acs_generator import GenerationEngine
except ModuleNotFoundError as exc:  # pragma: no cover - exercised only without the extra
    if exc.name == "acs_generator":
        raise ModuleNotFoundError(
            "The ACS policy generator requires the 'acs-generator' package. "
            'Install the ACS extra with: pip install "assert-ai[acs]"',
            name=exc.name,
        ) from exc
    raise

from assert_ai.integrations.acs.findings import FindingsSummary, load_findings
from assert_ai.integrations.acs.language_model import build_language_model
from assert_ai.integrations.acs.prompt_builder import build_guardrail_prompt

if TYPE_CHECKING:  # pragma: no cover - typing only
    from acs_generator import LanguageModel


@dataclass(frozen=True)
class PolicyArtifacts:
    """Paths and in-memory content of a generated ACS policy."""

    slug: str
    out_dir: Path
    manifest_path: Path
    rego_path: Path
    report_path: Path
    manifest: dict[str, Any]
    manifest_yaml: str
    rego: str
    report: str
    guarded_points: tuple[str, ...]
    warnings: tuple[str, ...]
    findings: FindingsSummary


def generate_policy(
    findings: FindingsSummary | str | Path,
    *,
    out_dir: str | Path,
    language_model: "LanguageModel | None" = None,
    lm_kind: str = "assert",
    model: str | None = None,
    tool_schema: list[dict[str, Any]] | None = None,
    strict: bool = False,
    write: bool = True,
    min_rate: float = 0.0,
    min_count: int = 1,
    max_examples_per_behavior: int = 3,
) -> PolicyArtifacts:
    """Generate an ACS policy from ASSERT findings.

    ``findings`` may be a :class:`FindingsSummary` or a path to an ASSERT run
    directory (containing ``scores.jsonl``); a path is loaded with
    :func:`load_findings` using ``min_rate``/``min_count``/``max_examples_per_behavior``.

    ``language_model`` overrides the model used by the generator. When omitted, one
    is built with :func:`build_language_model` from ``lm_kind`` (default
    ``"assert"``, which uses ASSERT's LiteLLM configuration) and ``model``.

    Requires the optional ``acs-generator`` dependency
    (``pip install "assert-ai[acs]"``).
    """
    summary = _resolve_findings(
        findings,
        min_rate=min_rate,
        min_count=min_count,
        max_examples_per_behavior=max_examples_per_behavior,
    )

    guardrail = build_guardrail_prompt(summary, tool_schema=tool_schema)
    lm = language_model if language_model is not None else build_language_model(lm_kind, model=model)

    out_path = Path(out_dir).expanduser()
    engine = GenerationEngine(lm)
    result = engine.generate(
        prompt=guardrail.prompt,
        out_dir=out_path,
        tool_inventory=guardrail.tool_inventory,
        strict=strict,
        write=write,
    )

    return PolicyArtifacts(
        slug=result.slug,
        out_dir=out_path,
        manifest_path=out_path / "manifest.yaml",
        rego_path=out_path / "policy" / f"{result.slug}.rego",
        report_path=out_path / "report.md",
        manifest=result.manifest,
        manifest_yaml=result.manifest_yaml,
        rego=result.rego,
        report=result.report,
        guarded_points=_declared_points(result.manifest) or guardrail.guarded_points,
        warnings=tuple(result.warnings),
        findings=summary,
    )


def _declared_points(manifest: dict[str, Any]) -> tuple[str, ...]:
    """Intervention points the generated manifest actually declares.

    The generator's LLM ultimately decides which points the manifest declares, so
    we report what the manifest contains rather than what the prompt requested,
    to avoid overstating coverage when a requested point is dropped.
    """
    points = manifest.get("intervention_points")
    if not isinstance(points, dict):
        return ()
    return tuple(str(name) for name in points)


def _resolve_findings(
    findings: FindingsSummary | str | Path,
    *,
    min_rate: float,
    min_count: int,
    max_examples_per_behavior: int,
) -> FindingsSummary:
    if isinstance(findings, FindingsSummary):
        return findings
    return load_findings(
        findings,
        min_rate=min_rate,
        min_count=min_count,
        max_examples_per_behavior=max_examples_per_behavior,
    )


__all__ = ["PolicyArtifacts", "generate_policy"]
