"""Generate a systematization artifact."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from p2m.core.config_model import ModelConfig
from p2m.core.io import load_prompt_text, write_json
from p2m.core.model_client import GenerateOptions, generate_structured

VALIDATION_CRITERIA_PROMPT = load_prompt_text("validation_criteria.md")
SYSTEMATIZATION_PROMPT = load_prompt_text("systematization_single.md").replace(
    "{validation_criteria}", VALIDATION_CRITERIA_PROMPT
)
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
    parent_slot_value: str
    component: str
    slot_values: list[SlotValue]

    model_config = ConfigDict(extra="forbid")


class SlotComponent(BaseModel):
    component: str
    nested_slot_components: list[NestedSlotComponent] | None
    slot_values: list[SlotValue]

    model_config = ConfigDict(extra="forbid")


class Pattern(BaseModel):
    pattern: str
    pattern_role: Literal["problematic", "acceptable"]
    primary_theory: str
    related_theory: str
    key_terms: list[KeyTerm]
    slot_components: list[SlotComponent]

    model_config = ConfigDict(extra="forbid")


class BehaviorSpec(BaseModel):
    behavior: str
    patterns: list[Pattern]

    model_config = ConfigDict(extra="forbid")


class ValidationItem(BaseModel):
    attribute: str
    score: str
    justification: str

    model_config = ConfigDict(extra="forbid")


class SystematizationResponse(BaseModel):
    behavior: str
    scope: str
    impact_analysis: str
    alternative_systematizations: str
    references: list[str]
    stakeholder_lenses: list[StakeholderLens]
    validation: list[ValidationItem]
    reasoning_summary: str
    concept_spec: BehaviorSpec

    model_config = ConfigDict(extra="forbid")


def systematization_json_schema() -> dict[str, Any]:
    return SystematizationResponse.model_json_schema()


def _build_prompt(*, behavior: str, behavior_text: str, context: str | None = None) -> str:
    parts = [
        f"{SYSTEMATIZATION_PROMPT}\n\n",
        "# Input\n",
        "The following is the actual behavior to systematize. Treat the label and body below as the real input, not as examples.\n\n",
        f"## Behavior Label\n{behavior}\n\n",
        f"## Background Behavior of Interest\n{behavior_text.strip()}\n",
    ]
    if context:
        parts.append(f"\n# Application Context\n{context.strip()}\n")
    return "".join(parts)


def _require_nonempty(value: str, field: str) -> None:
    if not value.strip():
        raise ValueError(f"systematization returned empty {field}")


def validate_systematization_response(parsed: SystematizationResponse, *, expected_behavior: str) -> None:
    _require_nonempty(parsed.behavior, "behavior")
    if parsed.behavior != expected_behavior:
        raise ValueError(
            "systematization behavior must match input behavior label: "
            f"expected {expected_behavior!r}, got {parsed.behavior!r}"
        )
    if parsed.concept_spec.behavior != parsed.behavior:
        raise ValueError("systematization concept_spec.behavior must match behavior")

    for field_name in (
        "scope",
        "impact_analysis",
        "alternative_systematizations",
        "reasoning_summary",
    ):
        _require_nonempty(str(getattr(parsed, field_name)), field_name)

    for index, reference in enumerate(parsed.references):
        _require_nonempty(reference, f"references[{index}]")
    for index, lens in enumerate(parsed.stakeholder_lenses):
        _require_nonempty(lens.label, f"stakeholder_lenses[{index}].label")
        _require_nonempty(lens.expertise, f"stakeholder_lenses[{index}].expertise")
    if not parsed.validation:
        raise ValueError("systematization validation must include at least one item")
    for index, item in enumerate(parsed.validation):
        _require_nonempty(item.attribute, f"validation[{index}].attribute")
        _require_nonempty(item.score, f"validation[{index}].score")
        _require_nonempty(item.justification, f"validation[{index}].justification")

    if not parsed.concept_spec.patterns:
        raise ValueError("systematization concept_spec.patterns must include at least one pattern")
    for pattern_index, pattern in enumerate(parsed.concept_spec.patterns):
        prefix = f"concept_spec.patterns[{pattern_index}]"
        _require_nonempty(pattern.pattern, f"{prefix}.pattern")
        _require_nonempty(pattern.primary_theory, f"{prefix}.primary_theory")
        _require_nonempty(pattern.related_theory, f"{prefix}.related_theory")
        for term_index, term in enumerate(pattern.key_terms):
            _require_nonempty(term.term, f"{prefix}.key_terms[{term_index}].term")
            _require_nonempty(term.definition, f"{prefix}.key_terms[{term_index}].definition")
        if not pattern.slot_components:
            raise ValueError(f"systematization {prefix}.slot_components must include at least one component")
        for component_index, component in enumerate(pattern.slot_components):
            component_prefix = f"{prefix}.slot_components[{component_index}]"
            _require_nonempty(component.component, f"{component_prefix}.component")
            if not component.slot_values:
                raise ValueError(f"systematization {component_prefix}.slot_values must include at least one value")
            for value_index, slot_value in enumerate(component.slot_values):
                value_prefix = f"{component_prefix}.slot_values[{value_index}]"
                _require_nonempty(slot_value.slot_value, f"{value_prefix}.slot_value")
                _require_nonempty(slot_value.definition, f"{value_prefix}.definition")
                _require_nonempty(slot_value.example_phrase, f"{value_prefix}.example_phrase")
            for nested_index, nested in enumerate(component.nested_slot_components or []):
                nested_prefix = f"{component_prefix}.nested_slot_components[{nested_index}]"
                _require_nonempty(nested.parent_slot_value, f"{nested_prefix}.parent_slot_value")
                _require_nonempty(nested.component, f"{nested_prefix}.component")
                if not nested.slot_values:
                    raise ValueError(f"systematization {nested_prefix}.slot_values must include at least one value")
                for value_index, slot_value in enumerate(nested.slot_values):
                    value_prefix = f"{nested_prefix}.slot_values[{value_index}]"
                    _require_nonempty(slot_value.slot_value, f"{value_prefix}.slot_value")
                    _require_nonempty(slot_value.definition, f"{value_prefix}.definition")
                    _require_nonempty(slot_value.example_phrase, f"{value_prefix}.example_phrase")


async def run_systematization(
    *,
    behavior: str,
    behavior_text: str,
    save_path: str,
    model_cfg: ModelConfig,
    mode: str = "research",
    web_search: bool = True,
    context: str | None = None,
) -> Path:
    """Generate one systematization artifact and persist it to disk."""
    if not behavior_text.strip():
        raise ValueError("systematization requires non-empty behavior text")
    if mode not in ALLOWED_MODES:
        raise ValueError(f"systematization.mode must be one of: {', '.join(sorted(ALLOWED_MODES))}")

    temperature = model_cfg.temperature
    # Reasoning models don't support temperature
    if model_cfg.reasoning_effort is not None:
        temperature = None

    response = await generate_structured(
        model_cfg.name,
        _build_prompt(behavior=behavior, behavior_text=behavior_text, context=context),
        schema_name="systematization",
        json_schema=systematization_json_schema(),
        options=GenerateOptions(
            temperature=temperature,
            max_tokens=model_cfg.max_tokens,
            web_search=web_search,
            reasoning_effort=model_cfg.reasoning_effort,
        ),
    )
    if getattr(response, "finish_reason", None) in ("length", "max_output_tokens"):
        raise ValueError(
            "systematization response was truncated (finish_reason=length). "
            "Increase max_tokens (current: {}) or reduce prompt complexity.".format(
                model_cfg.max_tokens
            )
        )
    payload = response.parsed
    if not isinstance(payload, dict) or not payload:
        if not response.text:
            raise ValueError("systematization returned no structured systematization")
        try:
            payload = json.loads(response.text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"systematization model returned unparseable output: {exc}. "
                f"Raw text (first 500 chars): {response.text[:500]}"
            ) from exc

    parsed = SystematizationResponse.model_validate(payload)
    validate_systematization_response(parsed, expected_behavior=behavior)

    artifact = parsed.model_dump()
    artifact["meta"] = {
        "mode": mode,
        "model": model_cfg.name,
        "reasoning_effort": model_cfg.reasoning_effort,
    }
    output_path = Path(save_path).expanduser().with_suffix(".json")
    write_json(output_path, artifact)
    return output_path
