"""Generate a systematization artifact."""

from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from p2m.core.config_model import ModelConfig
from p2m.core.io import load_prompt_text
from p2m.core.model_client import GenerateOptions, generate_structured

SYSTEMATIZATION_PROMPT = load_prompt_text("systematization_single.md")
ALLOWED_MODES = {"research", "direct"}


class SummaryItem(BaseModel):
    description: str
    example: str

    model_config = ConfigDict(extra="forbid")


class SystematizationResponse(BaseModel):
    systematization: str
    summary_items: list[SummaryItem]

    model_config = ConfigDict(extra="forbid")


def _humanize_concept_name(concept_name: str | None) -> str:
    return str(concept_name or "").replace("_", " ").replace("-", " ").strip()

def _build_prompt(*, concept: str, concept_text: str, context: str | None = None) -> str:
    parts = [
        f"{SYSTEMATIZATION_PROMPT}\n\n",
        f"# Concept Label\n{concept}\n\n",
        f"# Source Risk Text\n{concept_text.strip()}\n",
    ]
    if context:
        parts.append(f"\n# Application Context\n{context.strip()}\n")
    return "".join(parts)


def _extract_pattern_blocks(systematization: str) -> list[str]:
    """Split the systematization into individual pattern blocks.

    Each block starts with ``- **Pattern**:`` and extends until the next
    pattern bullet or the end of the patterns section.
    """
    parts = re.split(r"(?m)^- \*\*Pattern\*\*:", systematization)
    return [part.strip() for part in parts[1:] if part.strip()]


def _validate_pattern_block(block: str) -> None:
    """Validate a single slot-based pattern block."""
    if "**Key Terms**:" not in block:
        raise ValueError("systematization pattern block is missing Key Terms section")
    if "**Variables**:" not in block:
        raise ValueError("systematization pattern block is missing Variables section")
    slot_refs = re.findall(r"\[([A-Z][A-Z0-9_]*)\]", block.split("**Variables**:")[0])
    if not slot_refs:
        raise ValueError("systematization pattern template has no [SLOT] placeholders")
    variable_names = re.findall(r"\*\*\[([A-Z][A-Z0-9_]*)\]\*\*:\s*\{\{", block)
    if not variable_names:
        raise ValueError("systematization pattern has no {{ }} variable blocks")
    for slot in slot_refs:
        if slot not in variable_names:
            raise ValueError(f"systematization pattern has [SLOT] '{slot}' with no matching variable block")


def _validate_systematization(systematization: str) -> None:
    text = systematization.strip()
    if not text:
        raise ValueError("systematization returned empty systematization")
    required_headers = ["# Systematization", "## Scope", "## Coverage notes"]
    for header in required_headers:
        if header not in text:
            raise ValueError(f"systematization is missing required section: {header}")

    required_sections = [
        ("## Master inclusion / exclusion test", "Master inclusion / exclusion test"),
        ("## Severity calibration", "Severity calibration guide"),
        ("## Boundary examples", "Boundary examples"),
        ("## Worked scoring examples", "Worked scoring examples"),
        ("## Stakeholder guidance", "Stakeholder guidance"),
        ("## Fairness safeguard", "Fairness safeguards"),
        ("## Downstream harms", "Downstream harms"),
    ]
    for marker, name in required_sections:
        if marker not in text:
            raise ValueError(f"systematization is missing required section: {name}")

    blocks = _extract_pattern_blocks(text)
    if not blocks:
        raise ValueError("systematization must include at least one pattern block")

    if len(blocks) > 1 and "## Decision tree" not in text:
        raise ValueError("systematization with multiple patterns must include a Decision tree section")

    for block in blocks:
        _validate_pattern_block(block)


def _validate_summary_items(summary_items: list[SummaryItem]) -> None:
    if not summary_items:
        raise ValueError("systematization requires at least one summary item")
    for item in summary_items:
        if not item.description.strip():
            raise ValueError("systematization summary_items.description must be non-empty")
        if not item.example.strip():
            raise ValueError("systematization summary_items.example must be non-empty")


async def run_systematization(
    *,
    concept: str,
    concept_text: str,
    save_path: str,
    model_cfg: ModelConfig,
    mode: str = "research",
    web_search: bool = True,
    context: str | None = None,
) -> Path:
    """Generate one systematization artifact and persist it to disk."""
    if not concept_text.strip():
        raise ValueError("systematization requires non-empty concept text")
    if mode not in ALLOWED_MODES:
        raise ValueError(f"systematization.mode must be one of: {', '.join(sorted(ALLOWED_MODES))}")

    temperature = model_cfg.temperature
    # Reasoning models don't support temperature
    if model_cfg.reasoning_effort is not None:
        temperature = None

    response = await generate_structured(
        model_cfg.name,
        _build_prompt(concept=concept, concept_text=concept_text, context=context),
        schema_name="systematization",
        json_schema=SystematizationResponse.model_json_schema(),
        options=GenerateOptions(
            temperature=temperature,
            max_tokens=model_cfg.max_tokens,
            web_search=web_search,
            reasoning_effort=model_cfg.reasoning_effort,
        ),
    )
    if getattr(response, "finish_reason", None) == "length":
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
        payload = json.loads(response.text)

    parsed = SystematizationResponse.model_validate(payload)
    _validate_systematization(parsed.systematization)
    _validate_summary_items(parsed.summary_items)

    artifact = {
        "concept": concept,
        "systematization": parsed.systematization,
        "summary_items": [item.model_dump() for item in parsed.summary_items],
        "meta": {
            "mode": mode,
            "model": model_cfg.name,
            "reasoning_effort": model_cfg.reasoning_effort,
        },
    }
    output_path = Path(save_path).expanduser().with_suffix(".json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path
