# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Generate a systematization artifact."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Literal

log = logging.getLogger(__name__)

from pydantic import BaseModel, ConfigDict

from assert_ai.core.config_model import ModelConfig
from assert_ai.core.io import load_prompt_text
from assert_ai.core.llm_diagnostics import llm_failure_error
from assert_ai.core.model_client import (
    GenerateOptions,
    generate_structured,
    is_content_filtered_response,
    is_truncated_response,
)

SYSTEMATIZATION_PROMPT = load_prompt_text("systematization_single.md")
ALLOWED_MODES = {"research", "direct"}


class StakeholderLens(BaseModel):
    label: str
    expertise: str

    model_config = ConfigDict(extra="forbid")


class KeyTerm(BaseModel):
    term: str
    definition: str

    model_config = ConfigDict(extra="forbid")


class SlotValue(BaseModel):
    slot_value: str
    definition: str
    example_phrase: str

    model_config = ConfigDict(extra="forbid")


class NestedSlotComponent(BaseModel):
    component: str
    slot_values: list[SlotValue]

    model_config = ConfigDict(extra="forbid")


class SlotComponent(BaseModel):
    component: str
    nested_slot_components: list[NestedSlotComponent] | None
    slot_values: list[SlotValue]

    model_config = ConfigDict(extra="forbid")


class BehaviorPattern(BaseModel):
    pattern: str
    pattern_role: Literal["problematic", "acceptable"]
    primary_theory: str
    related_theory: str
    key_terms: list[KeyTerm]
    slot_components: list[SlotComponent]

    model_config = ConfigDict(extra="forbid")


class ConceptSpec(BaseModel):
    behavior: str
    patterns: list[BehaviorPattern]

    model_config = ConfigDict(extra="forbid")


class SystematizationResponse(BaseModel):
    """Structured output contract mirrored by systematization_single.md."""

    behavior: str
    scope: str
    impact_analysis: str
    alternative_systematizations: str
    references: list[str]
    stakeholder_lenses: list[StakeholderLens]
    reasoning_summary: str
    concept_spec: ConceptSpec

    model_config = ConfigDict(extra="forbid")

def _build_prompt(*, behavior: str, behavior_text: str, context: str | None = None) -> str:
    parts = [
        f"{SYSTEMATIZATION_PROMPT}\n\n",
        "# Input\n",
        "The following is the actual behavior to systematize. Treat the label and body below as the real input, not as examples.\n\n",
        f"## Behavior Label\n{behavior}\n\n",
        f"## Background Behavior of Interest\n{behavior_text.strip()}\n",
    ]
    if context:
        parts.append(f"\n## Application Context\n{context.strip()}\n")
    return "".join(parts)


async def run_systematization(
    *,
    behavior: str,
    behavior_text: str,
    save_path: str,
    model_cfg: ModelConfig,
    mode: str = "research",
    web_search: bool = True,
    context: str | None = None,
    diagnostics_dir: str | Path | None = None,
) -> Path:
    """Generate one systematization artifact and persist it to disk.

    Single attempt: a truncated response (finish_reason indicating the
    output budget was exhausted, on either Chat Completions or the
    Responses API) raises a clear actionable error pointing the user at
    ``pipeline.systematize.model.max_tokens``. See issue #131 for the original
    repro on the travel-planner example, which surfaced as an opaque
    ``json.JSONDecodeError`` because the truncation detector only knew
    about the Chat Completions value.
    """
    if not behavior_text.strip():
        raise ValueError("systematization requires non-empty behavior text")
    if mode not in ALLOWED_MODES:
        raise ValueError(f"systematization.mode must be one of: {', '.join(sorted(ALLOWED_MODES))}")
    log.debug(f"systematization: behavior={behavior}, model={model_cfg.name}, mode={mode}, web_search={web_search}")
    output_path = Path(save_path).expanduser().with_suffix(".json")
    diagnostic_root = Path(diagnostics_dir).expanduser() if diagnostics_dir else output_path.parent / "diagnostics"

    temperature = model_cfg.temperature
    # Reasoning models don't support temperature
    if model_cfg.reasoning_effort is not None:
        temperature = None

    response = await generate_structured(
        model_cfg.name,
        _build_prompt(behavior=behavior, behavior_text=behavior_text, context=context),
        schema_name="systematization",
        json_schema=SystematizationResponse.model_json_schema(),
        options=GenerateOptions(
            temperature=temperature,
            max_tokens=model_cfg.max_tokens,
            web_search=web_search,
            reasoning_effort=model_cfg.reasoning_effort,
        ),
    )
    if is_content_filtered_response(response):
        raise llm_failure_error(
            response,
            diagnostics_dir=diagnostic_root,
            stage="systematization",
            reason="content_filtered",
            attempt=1,
            message=(
                "systematization response was stopped by the provider content filter "
                "before the structured document completed. This is not a token or quota "
                "failure. ASSERT requests masked, provider-safe examples; if this persists, "
                "use a model deployment approved for this evaluation content."
            ),
        )
    if is_truncated_response(response):
        finish_reason = getattr(response, "finish_reason", None)
        raise llm_failure_error(
            response,
            diagnostics_dir=diagnostic_root,
            stage="systematization",
            reason="output_truncated",
            attempt=1,
            message=(
                "systematization response was truncated by the model's output budget "
                f"(finish_reason={finish_reason!r}, max_tokens={model_cfg.max_tokens}). "
                "Increase pipeline.systematize.model.max_tokens (or remove the override "
                "to use the default) or simplify the behavior description."
            ),
        )
    payload = response.parsed
    if not isinstance(payload, dict) or not payload:
        if not response.text:
            raise llm_failure_error(
                response,
                diagnostics_dir=diagnostic_root,
                stage="systematization",
                reason="empty_structured_output",
                attempt=1,
                message="systematization returned no structured systematization",
            )
        try:
            payload = json.loads(response.text)
        except json.JSONDecodeError as exc:
            raise llm_failure_error(
                response,
                diagnostics_dir=diagnostic_root,
                stage="systematization",
                reason="unparseable_output",
                attempt=1,
                message=(
                    f"systematization model returned unparseable output: {exc}. "
                    f"Raw text (first 500 chars): {response.text[:500]}"
                ),
            ) from exc

    try:
        parsed = SystematizationResponse.model_validate(payload)
        if parsed.behavior != behavior or parsed.concept_spec.behavior != behavior:
            raise ValueError(
                "systematization behavior labels must exactly match the input behavior "
                f"{behavior!r}"
            )
    except ValueError as exc:
        raise llm_failure_error(
            response,
            diagnostics_dir=diagnostic_root,
            stage="systematization",
            reason="schema_validation_failed",
            attempt=1,
            message=str(exc),
        ) from exc

    artifact = {
        "behavior": behavior,
        "systematization": parsed.model_dump(mode="json"),
        "meta": {
            "mode": mode,
            "model": model_cfg.name,
            "reasoning_effort": model_cfg.reasoning_effort,
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path
