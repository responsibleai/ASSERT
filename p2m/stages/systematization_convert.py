"""Convert a systematization artifact to a structured taxonomy JSON."""

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
from p2m.stages.taxonomy import taxonomy_schema

BASE_DIR = Path(__file__).resolve().parents[2]
GUIDELINE_PROMPT = (BASE_DIR / "prompts" / "systematization_convert_single.md").read_text(encoding="utf-8")

TAXONOMY_SCHEMA: dict[str, Any] = taxonomy_schema()
DEFAULT_FAILURE_MODE_COUNT_HINT = 30


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


def _normalize_failure_modes(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise ValueError("systematization_convert returned invalid failure_modes")
    failure_modes = []
    for failure_mode_payload in payload:
        if not isinstance(failure_mode_payload, dict):
            raise ValueError("systematization_convert returned invalid failure_modes")
        permissible = failure_mode_payload.get("permissible")
        if not isinstance(permissible, bool):
            raise ValueError("systematization_convert returned invalid failure_modes.permissible")
        failure_modes.append(
            {
                "name": _require_nonempty_string(failure_mode_payload.get("name"), field="failure_modes.name"),
                "definition": _require_nonempty_string(
                    failure_mode_payload.get("definition"),
                    field="failure_modes.definition",
                ),
                "examples": _require_examples(
                    failure_mode_payload.get("examples"),
                    field="failure_modes.examples",
                ),
                "permissible": permissible,
            }
        )
    if not failure_modes:
        raise ValueError("systematization_convert returned no failure_modes")
    return failure_modes


async def run_systematization_to_taxonomy(
    *,
    systematization_path: str,
    save_path: str = "artifacts/taxonomies/taxonomy.json",
    model_cfg: ModelConfig | None = None,
    failure_mode_count_hint: int = DEFAULT_FAILURE_MODE_COUNT_HINT,
) -> Path:
    """Convert the systematization artifact into the taxonomy JSON artifact."""
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
    spec = str(data.get("spec") or "").strip()
    if not spec:
        raise ValueError("systematization_convert requires systematization.json to include spec")
    systematization_text = str(data.get("systematization") or "").strip()
    if not systematization_text:
        raise ValueError("systematization_convert requires a non-empty systematization")
    summary_items = data.get("summary_items")
    if summary_items is not None and not isinstance(summary_items, list):
        raise ValueError("systematization_convert requires summary_items to be a list when present")

    prompt = (
        GUIDELINE_PROMPT.replace("{{failure_mode_count}}", str(failure_mode_count_hint))
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
        schema_name="taxonomy",
        json_schema=TAXONOMY_SCHEMA,
        options=GenerateOptions(
            temperature=temperature,
            max_tokens=model_cfg.max_tokens,
            reasoning_effort=model_cfg.reasoning_effort,
        ),
    )
    taxonomy_payload = response.parsed
    if not isinstance(taxonomy_payload, dict) or not taxonomy_payload:
        raise ValueError("systematization_convert returned no structured taxonomy")

    spec_block = taxonomy_payload.get("spec")
    if not isinstance(spec_block, dict):
        raise ValueError("systematization_convert returned invalid spec")
    spec_definition = _require_nonempty_string(spec_block.get("definition"), field="spec.definition")
    terms = _normalize_definition_of_terms(taxonomy_payload.get("definition_of_terms"))
    failure_modes = _normalize_failure_modes(taxonomy_payload.get("failure_modes"))
    taxonomy = {
        "spec": {
            "name": spec,
            "definition": spec_definition,
        },
        "definition_of_terms": terms,
        "failure_modes": failure_modes,
        "meta": {
            "source": "systematization",
            "systematization_path": systematization_path,
            "slug": spec,
            "run_id": uuid.uuid4().hex[:8],
        },
    }
    output_path = Path(save_path).expanduser()
    if not output_path.is_absolute():
        output_path = BASE_DIR / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(taxonomy, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path
