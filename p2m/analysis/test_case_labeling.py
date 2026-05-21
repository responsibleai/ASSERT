"""Post-generation labeling of test_set against stratification dimensions.

LLM-driven classification: given generated test_set and a stratification catalog,
assign one level per dimension to each test case.
"""

from __future__ import annotations

from typing import Any

from p2m.core.async_utils import gather_limited
from p2m.core.model_client import GenerateOptions, generate_structured
from p2m.core.io import fill_template
from p2m.stages.stratification import render_stratification_catalog

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_GENERATION_MAX_TOKENS = 50_000

LABELING_PROMPT_TEMPLATE = """# Task

Assign exactly one catalog level name for each dimension to each generated test case.

Label the realized test case as written, not the likely author intent. Use only
the level names from the catalog below.

# Inputs

- Behavior: {{behavior_name}}

# Catalog

{{stratification_catalog}}

# Test Set

{{test_case_batch}}

# Rules

1. Output labels for every test case in the same order they are listed.
2. Choose exactly one level name for each dimension: {{axis_list}}.
3. Use the closest matching level name when a test case is mixed or underspecified.
4. For `behavior`, classify from the test-case content and taxonomy-behavior
   definitions, not from any presumed source batch.
5. For `system_configuration`, infer from the system prompt field if present,
   or from the message framing if not.
6. Do not return explanations, confidence scores, or extra keys.

# Output Contract

Output exactly one JSON object with key `labels`. The value must be an array
with exactly {{count}} objects in test-case order.
"""


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


def _label_entry_schema(
    stratification: dict[str, list[dict[str, str]]],
) -> dict[str, Any]:
    dimensions = tuple(key for key in stratification if not key.startswith("_"))
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            factor_name: {
                "type": "string",
                "enum": [entry["name"] for entry in stratification[factor_name]],
            }
            for factor_name in dimensions
        },
        "required": list(dimensions),
    }


def _labels_response_schema(
    stratification: dict[str, list[dict[str, str]]],
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
                "items": _label_entry_schema(stratification),
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
        test_case_payload = row.get("seed") or {}
        system_prompt = str(test_case_payload.get("system_prompt") or "").strip()
        lines = [
            f"{label} {index}:",
            f"- Title: {str(test_case_payload.get('title') or '').strip() or '(empty)'}",
            f"- {desc_label}: {str(test_case_payload.get('description') or '').strip()}",
            f"- System prompt: {system_prompt or '(none)'}",
        ]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def build_labeling_prompt(
    *,
    kind: str,
    behavior_name: str,
    stratification: dict[str, list[dict[str, str]]],
    rows: list[dict[str, Any]],
) -> str:
    dimensions = tuple(key for key in stratification if not key.startswith("_"))
    axis_list = ", ".join(f"`{dimension}`" for dimension in dimensions)
    return fill_template(
        LABELING_PROMPT_TEMPLATE,
        {
            "behavior_name": behavior_name,
            "stratification_catalog": render_stratification_catalog(
                stratification, include_behavior="behavior" in stratification
            ),
            "test_case_batch": _render_labeling_batch(rows, kind=kind),
            "count": str(len(rows)),
            "axis_list": axis_list,
        },
    )


# ---------------------------------------------------------------------------
# Label normalization
# ---------------------------------------------------------------------------


def _normalize_observed_label_entry(
    entry: Any,
    stratification: dict[str, list[dict[str, str]]],
) -> dict[str, str]:
    if not isinstance(entry, dict):
        raise ValueError("observed label entry must be a JSON object")
    dimensions = tuple(key for key in stratification if not key.startswith("_"))
    extra_keys = set(entry) - set(dimensions)
    if extra_keys:
        raise ValueError(
            f"observed label entry contains unexpected keys: "
            f"{', '.join(sorted(extra_keys))}"
        )
    normalized: dict[str, str] = {}
    for factor_name in dimensions:
        value = str(entry.get(factor_name) or "").strip()
        valid_names = {d["name"] for d in stratification[factor_name]}
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
    behavior_name: str,
    stratification: dict[str, list[dict[str, str]]],
    rows: list[dict[str, Any]],
    labeling_batch_size: int,
    concurrency: int,
) -> list[dict[str, str]]:
    """Run post-hoc labeling on generated test case rows."""
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
            behavior_name=behavior_name,
            stratification=stratification,
            rows=batch_rows,
        )
        response = await generate_structured(
            model,
            prompt,
            schema_name="test_case_labels",
            json_schema=_labels_response_schema(stratification, len(batch_rows)),
            options=GenerateOptions(
                temperature=None,
                max_tokens=DEFAULT_GENERATION_MAX_TOKENS,
            ),
        )
        payload = response.parsed
        if not isinstance(payload, dict) or not isinstance(
            payload.get("labels"), list
        ):
            raise ValueError("test-case labeling returned invalid payload")
        if len(payload["labels"]) != len(batch_rows):
            raise ValueError(
                f"test-case labeling returned {len(payload['labels'])} labels "
                f"for a batch of {len(batch_rows)}"
            )
        labeled = []
        for row, raw_label in zip(
            batch_rows, payload["labels"], strict=True
        ):
            labeled.append(
                {
                    "test_case_id": str(row["test_case_id"]),
                    **_normalize_observed_label_entry(raw_label, stratification),
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
