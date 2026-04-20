"""Convert a systematization artifact to a structured policy JSON."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from p2m.core.config_model import (
    DEFAULT_SYSTEMATIZATION_CONVERT_MAX_TOKENS,
    DEFAULT_SYSTEMATIZATION_CONVERT_TEMPERATURE,
    DEFAULT_SYSTEMATIZATION_MODEL,
    ModelConfig,
)
from p2m.core.model_client import GenerateOptions, generate_structured
from p2m.stages.policy import policy_schema

BASE_DIR = Path(__file__).resolve().parents[2]
GUIDELINE_PROMPT = (BASE_DIR / "prompts" / "systematization_convert_single.md").read_text()

POLICY_SCHEMA: dict[str, Any] = policy_schema()
DEFAULT_BEHAVIOR_COUNT_HINT = 30


def _require_nonempty_string(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"systematization_convert returned invalid {field}")
    return value.strip()


def _require_examples(value: Any, *, field: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"systematization_convert returned invalid {field}")
    examples = []
    for example in value:
        if not isinstance(example, str) or not example.strip():
            raise ValueError(f"systematization_convert returned invalid {field}")
        examples.append(example.strip())
    if not examples:
        raise ValueError(f"systematization_convert returned invalid {field}")
    return examples


def _normalize_definition_of_terms(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise ValueError("systematization_convert returned invalid definition_of_terms")
    terms = []
    for term_payload in payload:
        if not isinstance(term_payload, dict):
            raise ValueError("systematization_convert returned invalid definition_of_terms")
        terms.append(
            {
                "term": _require_nonempty_string(term_payload.get("term"), field="definition_of_terms.term"),
                "definition": _require_nonempty_string(
                    term_payload.get("definition"),
                    field="definition_of_terms.definition",
                ),
                "examples": _require_examples(
                    term_payload.get("examples"),
                    field="definition_of_terms.examples",
                ),
            }
        )
    return terms


def _normalize_behaviors(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise ValueError("systematization_convert returned invalid behaviors")
    behaviors = []
    for behavior_payload in payload:
        if not isinstance(behavior_payload, dict):
            raise ValueError("systematization_convert returned invalid behaviors")
        permissible = behavior_payload.get("permissible")
        if not isinstance(permissible, bool):
            raise ValueError("systematization_convert returned invalid behaviors.permissible")
        behaviors.append(
            {
                "name": _require_nonempty_string(behavior_payload.get("name"), field="behaviors.name"),
                "definition": _require_nonempty_string(
                    behavior_payload.get("definition"),
                    field="behaviors.definition",
                ),
                "examples": _require_examples(
                    behavior_payload.get("examples"),
                    field="behaviors.examples",
                ),
                "permissible": permissible,
            }
        )
    if not behaviors:
        raise ValueError("systematization_convert returned no behaviors")
    return behaviors


async def run_systematization_to_policy(
    *,
    systematization_path: str,
    save_path: str = "artifacts/taxonomies/policy.json",
    model_cfg: ModelConfig | None = None,
    behavior_count_hint: int = DEFAULT_BEHAVIOR_COUNT_HINT,
) -> Path:
    """Convert the systematization artifact into the policy JSON artifact."""
    if model_cfg is None:
        model_cfg = ModelConfig(
            name=DEFAULT_SYSTEMATIZATION_MODEL,
            temperature=DEFAULT_SYSTEMATIZATION_CONVERT_TEMPERATURE,
            max_tokens=DEFAULT_SYSTEMATIZATION_CONVERT_MAX_TOKENS,
        )
    data_path = Path(systematization_path).expanduser()
    if not data_path.is_absolute():
        data_path = BASE_DIR / data_path
    data = json.loads(data_path.read_text(encoding="utf-8"))
    concept = str(data.get("concept") or "").strip()
    if not concept:
        raise ValueError("systematization_convert requires systematization.json to include concept")
    systematization_text = str(data.get("systematization") or "").strip()
    if not systematization_text:
        raise ValueError("systematization_convert requires a non-empty systematization")
    summary_items = data.get("summary_items")
    if summary_items is not None and not isinstance(summary_items, list):
        raise ValueError("systematization_convert requires summary_items to be a list when present")

    prompt = (
        GUIDELINE_PROMPT.replace("{{behavior_count}}", str(behavior_count_hint))
        + "\n\n# SYSTEMATIZATION\n"
        + systematization_text
    )
    if summary_items:
        prompt += "\n\n# SUMMARY ITEMS\n" + json.dumps(summary_items, ensure_ascii=False, indent=2)
    temperature = model_cfg.temperature
    # Reasoning models don't support temperature
    if model_cfg.reasoning_effort is not None:
        temperature = None
    response = await generate_structured(
        model_cfg.name,
        prompt,
        schema_name="policy",
        json_schema=POLICY_SCHEMA,
        options=GenerateOptions(
            temperature=temperature,
            max_tokens=model_cfg.max_tokens,
            reasoning_effort=model_cfg.reasoning_effort,
        ),
    )
    policy_payload = response.parsed
    if not isinstance(policy_payload, dict) or not policy_payload:
        raise ValueError("systematization_convert returned no structured policy")

    concept_block = policy_payload.get("concept")
    if not isinstance(concept_block, dict):
        raise ValueError("systematization_convert returned invalid concept")
    concept_definition = _require_nonempty_string(concept_block.get("definition"), field="concept.definition")
    terms = _normalize_definition_of_terms(policy_payload.get("definition_of_terms"))
    behaviors = _normalize_behaviors(policy_payload.get("behaviors"))
    policy = {
        "concept": {
            "name": concept,
            "definition": concept_definition,
        },
        "definition_of_terms": terms,
        "behaviors": behaviors,
        "meta": {
            "source": "systematization",
            "systematization_path": systematization_path,
            "slug": concept,
            "run_id": uuid.uuid4().hex[:8],
        },
    }
    output_path = Path(save_path).expanduser()
    if not output_path.is_absolute():
        output_path = BASE_DIR / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(policy, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path
