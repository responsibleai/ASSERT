# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Convert a systematization artifact to a structured taxonomy JSON."""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

from assert_eval.core.config_model import (
    DEFAULT_SYSTEMATIZATION_CONVERT_MAX_TOKENS,
    DEFAULT_SYSTEMATIZATION_CONVERT_TEMPERATURE,
    DEFAULT_SYSTEMATIZATION_MODEL,
    ModelConfig,
)
from assert_eval.core.model_client import GenerateOptions, generate_structured, is_truncated_response
from assert_eval.stages.systematize import taxonomy_schema

BASE_DIR = Path(__file__).resolve().parents[2]
GUIDELINE_PROMPT = (BASE_DIR / "prompts" / "systematization_convert_single.md").read_text(encoding="utf-8")

TAXONOMY_SCHEMA: dict[str, Any] = taxonomy_schema()
DEFAULT_BEHAVIOR_CATEGORY_COUNT_HINT = 30


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


def _normalize_behavior_categories(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise ValueError("systematization_convert returned invalid behavior_categories")
    behavior_categories = []
    for behavior_payload in payload:
        if not isinstance(behavior_payload, dict):
            raise ValueError("systematization_convert returned invalid behavior_categories")
        permissible = behavior_payload.get("permissible")
        if not isinstance(permissible, bool):
            raise ValueError("systematization_convert returned invalid behavior_categories.permissible")
        behavior_categories.append(
            {
                "name": _require_nonempty_string(behavior_payload.get("name"), field="behavior_categories.name"),
                "definition": _require_nonempty_string(
                    behavior_payload.get("definition"),
                    field="behavior_categories.definition",
                ),
                "examples": _require_examples(
                    behavior_payload.get("examples"),
                    field="behavior_categories.examples",
                ),
                "permissible": permissible,
            }
        )
    if not behavior_categories:
        raise ValueError("systematization_convert returned no behavior_categories")
    return behavior_categories


async def run_systematization_to_taxonomy(
    *,
    systematization_path: str,
    save_path: str = "artifacts/taxonomies/taxonomy.json",
    model_cfg: ModelConfig | None = None,
    behavior_category_count_hint: int = DEFAULT_BEHAVIOR_CATEGORY_COUNT_HINT,
) -> Path:
    """Convert the systematization artifact into the taxonomy JSON artifact."""
    if model_cfg is None:
        model_cfg = ModelConfig(
            name=DEFAULT_SYSTEMATIZATION_MODEL,
            temperature=DEFAULT_SYSTEMATIZATION_CONVERT_TEMPERATURE,
            max_tokens=DEFAULT_SYSTEMATIZATION_CONVERT_MAX_TOKENS,
        )
    log.debug(f"systematization_convert: model={model_cfg.name}, behavior_category_count_hint={behavior_category_count_hint}")
    data_path = Path(systematization_path).expanduser()
    if not data_path.is_absolute():
        data_path = BASE_DIR / data_path
    try:
        data = json.loads(data_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise FileNotFoundError(f"Systematization file not found: {data_path}") from None
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid JSON in systematization file {data_path}: {exc}"
        ) from exc
    behavior = str(data.get("behavior") or "").strip()
    if not behavior:
        raise ValueError("systematization_convert requires systematization.json to include behavior")
    systematization_text = str(data.get("systematization") or "").strip()
    if not systematization_text:
        raise ValueError("systematization_convert requires a non-empty systematization")
    summary_items = data.get("summary_items")
    if summary_items is not None and not isinstance(summary_items, list):
        raise ValueError("systematization_convert requires summary_items to be a list when present")

    prompt = (
        GUIDELINE_PROMPT.replace("{{behavior_category_count}}", str(behavior_category_count_hint))
        + "\n\n# SYSTEMATIZATION\n"
        + systematization_text
    )
    if summary_items:
        prompt += "\n\n# SUMMARY ITEMS\n" + json.dumps(summary_items, ensure_ascii=False, indent=2)
    temperature = model_cfg.temperature
    # Reasoning models don't support temperature
    if model_cfg.reasoning_effort is not None:
        temperature = None

    # Retry structured generation up to 2 times if the model returns
    # a response that doesn't parse into a valid taxonomy dict.  This is
    # a transient LLM output quality issue — the model occasionally
    # produces malformed JSON even with response_format set.
    #
    # NOTE: this retry is at the SAME max_tokens budget. If the failure
    # mode is the model exhausting its output budget (truncation), the
    # second attempt will hit the same wall, so we surface a clear
    # truncation-specific error pointing the user at max_tokens rather
    # than the generic "transient model issue" message. See issue #131.
    _MAX_PARSE_ATTEMPTS = 2
    taxonomy_payload: dict[str, Any] | None = None
    last_text = ""
    saw_truncation = False
    last_response = None
    for _attempt in range(_MAX_PARSE_ATTEMPTS):
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
        last_response = response
        if is_truncated_response(response):
            saw_truncation = True
        if isinstance(response.parsed, dict) and response.parsed:
            taxonomy_payload = response.parsed
            break
        last_text = response.text or ""
        if _attempt < _MAX_PARSE_ATTEMPTS - 1:
            logging.warning(
                "Taxonomy generation returned unparseable output (attempt %d/%d), retrying",
                _attempt + 1,
                _MAX_PARSE_ATTEMPTS,
            )

    if not isinstance(taxonomy_payload, dict) or not taxonomy_payload:
        if saw_truncation:
            finish_reason = getattr(last_response, "finish_reason", None) if last_response is not None else None
            raise ValueError(
                "systematization_convert response was truncated by the model's "
                f"output budget (finish_reason={finish_reason!r}, "
                f"max_tokens={model_cfg.max_tokens}) after {_MAX_PARSE_ATTEMPTS} attempts. "
                "Increase pipeline.systematize.model.max_tokens (or remove the override "
                "to use the default) or simplify the systematization input."
            )
        raise ValueError(
            f"systematization_convert returned no structured taxonomy after "
            f"{_MAX_PARSE_ATTEMPTS} attempts (last response: {last_text[:200]}). "
            f"This is usually a transient model issue — rerun the command to retry. "
            f"If it persists, check your endpoint's token rate limit and quota."
        )

    behavior_block = taxonomy_payload.get("behavior")
    if not isinstance(behavior_block, dict):
        raise ValueError("systematization_convert returned invalid behavior")
    behavior_definition = _require_nonempty_string(behavior_block.get("definition"), field="behavior.definition")
    terms = _normalize_definition_of_terms(taxonomy_payload.get("definition_of_terms"))
    behavior_categories = _normalize_behavior_categories(taxonomy_payload.get("behavior_categories"))
    taxonomy = {
        "behavior": {
            "name": behavior,
            "definition": behavior_definition,
        },
        "definition_of_terms": terms,
        "behavior_categories": behavior_categories,
        "meta": {
            "source": "systematization",
            "systematization_path": systematization_path,
            "slug": behavior,
            "run_id": uuid.uuid4().hex[:8],
        },
    }
    output_path = Path(save_path).expanduser()
    if not output_path.is_absolute():
        output_path = BASE_DIR / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(taxonomy, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path
