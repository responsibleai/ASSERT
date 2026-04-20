"""Post-generation labeling of seeds against design factors.

LLM-driven classification: given generated seeds and a design catalog,
assign one level per factor to each seed.
"""

from __future__ import annotations

from typing import Any

from p2m.core.async_utils import gather_limited
from p2m.core.model_client import GenerateOptions, generate_structured
from p2m.core.io import fill_template
from p2m.stages.design import render_design_catalog

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_GENERATION_MAX_TOKENS = 50_000

LABELING_PROMPT_TEMPLATE = """# Task

Assign exactly one catalog level name for each factor to each generated seed.

Label the realized seed as written, not the likely author intent. Use only
the level names from the catalog below.

# Inputs

- Risk: {{concept_name}}

# Catalog

{{design_catalog}}

# Seeds

{{seed_batch}}

# Rules

1. Output labels for every seed in the same order they are listed.
2. Choose exactly one level name for each factor: {{axis_list}}.
3. Use the closest matching level name when a seed is mixed or underspecified.
4. For `behavior`, classify from the seed content and policy-behavior
   definitions, not from any presumed source batch.
5. For `system_configuration`, infer from the system prompt field if present,
   or from the message framing if not.
6. Do not return explanations, confidence scores, or extra keys.

# Output Contract

Output exactly one JSON object with key `labels`. The value must be an array
with exactly {{count}} objects in seed order.
"""


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


def _label_entry_schema(
    design: dict[str, list[dict[str, str]]],
) -> dict[str, Any]:
    factors = tuple(key for key in design if not key.startswith("_"))
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            factor_name: {
                "type": "string",
                "enum": [entry["name"] for entry in design[factor_name]],
            }
            for factor_name in factors
        },
        "required": list(factors),
    }


def _labels_response_schema(
    design: dict[str, list[dict[str, str]]],
    count: int,
) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "labels": {
                "type": "array",
                "minItems": count,
                "maxItems": count,
                "items": _label_entry_schema(design),
            }
        },
        "required": ["labels"],
    }


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


def _render_labeling_batch(
    rows: list[dict[str, Any]], *, kind: str
) -> str:
    label = "Prompt" if kind == "prompt" else "Scenario"
    desc_label = "Message" if kind == "prompt" else "Description"
    blocks: list[str] = []
    for index, row in enumerate(rows, 1):
        seed = row.get("seed") or {}
        system_prompt = str(seed.get("system_prompt") or "").strip()
        lines = [
            f"{label} {index}:",
            f"- Title: {str(seed.get('title') or '').strip() or '(empty)'}",
            f"- {desc_label}: {str(seed.get('description') or '').strip()}",
            f"- System prompt: {system_prompt or '(none)'}",
        ]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def build_labeling_prompt(
    *,
    kind: str,
    concept_name: str,
    design: dict[str, list[dict[str, str]]],
    rows: list[dict[str, Any]],
) -> str:
    factors = tuple(key for key in design if not key.startswith("_"))
    axis_list = ", ".join(f"`{factor}`" for factor in factors)
    return fill_template(
        LABELING_PROMPT_TEMPLATE,
        {
            "concept_name": concept_name,
            "design_catalog": render_design_catalog(
                design, include_behavior="behavior" in design
            ),
            "seed_batch": _render_labeling_batch(rows, kind=kind),
            "count": str(len(rows)),
            "axis_list": axis_list,
        },
    )


# ---------------------------------------------------------------------------
# Label normalization
# ---------------------------------------------------------------------------


def _normalize_observed_label_entry(
    entry: Any,
    design: dict[str, list[dict[str, str]]],
) -> dict[str, str]:
    if not isinstance(entry, dict):
        raise ValueError("observed label entry must be a JSON object")
    factors = tuple(key for key in design if not key.startswith("_"))
    extra_keys = set(entry) - set(factors)
    if extra_keys:
        raise ValueError(
            f"observed label entry contains unexpected keys: "
            f"{', '.join(sorted(extra_keys))}"
        )
    normalized: dict[str, str] = {}
    for factor_name in factors:
        value = str(entry.get(factor_name) or "").strip()
        valid_names = {d["name"] for d in design[factor_name]}
        if value not in valid_names:
            raise ValueError(
                f"observed label entry has invalid {factor_name}: {value}"
            )
        normalized[factor_name] = value
    return normalized


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


async def label_generated_rows(
    *,
    kind: str,
    model: str,
    concept_name: str,
    design: dict[str, list[dict[str, str]]],
    rows: list[dict[str, Any]],
    labeling_batch_size: int,
    concurrency: int,
) -> list[dict[str, str]]:
    """Run post-hoc labeling on generated seed rows."""
    if not rows:
        return []

    batches = [
        rows[s : s + labeling_batch_size] for s in range(0, len(rows), labeling_batch_size)
    ]

    async def _process(
        batch_index: int, batch_rows: list[dict[str, Any]]
    ) -> dict[str, Any]:
        prompt = build_labeling_prompt(
            kind=kind,
            concept_name=concept_name,
            design=design,
            rows=batch_rows,
        )
        response = await generate_structured(
            model,
            prompt,
            schema_name="policy_seed_labels",
            json_schema=_labels_response_schema(design, len(batch_rows)),
            options=GenerateOptions(
                temperature=None,
                max_tokens=DEFAULT_GENERATION_MAX_TOKENS,
            ),
        )
        payload = response.parsed
        if not isinstance(payload, dict) or not isinstance(
            payload.get("labels"), list
        ):
            raise ValueError("seed labeling returned invalid payload")
        if len(payload["labels"]) != len(batch_rows):
            raise ValueError(
                f"seed labeling returned {len(payload['labels'])} labels "
                f"for a batch of {len(batch_rows)}"
            )
        labeled = []
        for row, raw_label in zip(
            batch_rows, payload["labels"], strict=True
        ):
            labeled.append(
                {
                    "seed_id": str(row["seed_id"]),
                    **_normalize_observed_label_entry(raw_label, design),
                }
            )
        return {"order": batch_index, "rows": labeled}

    results = await gather_limited(
        [{"order": i, "rows": b} for i, b in enumerate(batches)],
        limit=concurrency,
        worker=lambda job: _process(job["order"], job["rows"]),
    )
    ordered = sorted(results, key=lambda r: r["order"])
    return [row for result in ordered for row in result["rows"]]
