"""Generate a taxonomy artifact from a spec description."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from p2m.config import parse_model_config, resolve_stage_paths
from p2m.core.config_model import (
    DEFAULT_TAXONOMY_MAX_TOKENS,
    DEFAULT_TAXONOMY_TEMPERATURE,
)
from p2m.core.io import write_json
from p2m.core.model_client import GenerateOptions, Message, generate_structured

BASE_DIR = Path(__file__).resolve().parents[2]
GEN_PROMPT = (BASE_DIR / "prompts" / "taxonomy_system.md").read_text()
DEFAULT_FAILURE_MODE_COUNT = 25
MIN_TAXONOMY_FAILURE_MODES = 5

SCOPE = "suite"
SUITE_OUTPUT = "taxonomy.json"


def taxonomy_schema(*, min_failure_modes: int = 0) -> Dict[str, Any]:
    """JSON schema for a risk taxonomy with failure_modes and term definitions."""
    failure_modes_prop: Dict[str, Any] = {
        "type": "array",
        "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "name": {"type": "string"},
                "definition": {"type": "string"},
                "examples": {"type": "array", "items": {"type": "string"}},
                "permissible": {"type": "boolean"},
            },
            "required": ["name", "definition", "examples", "permissible"],
        },
    }
    if min_failure_modes > 0:
        failure_modes_prop["minItems"] = min_failure_modes
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "spec": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "definition": {"type": "string"},
                },
                "required": ["name", "definition"],
            },
            "definition_of_terms": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "term": {"type": "string"},
                        "definition": {"type": "string"},
                        "examples": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["term", "definition", "examples"],
                },
            },
            "failure_modes": failure_modes_prop,
        },
        "required": ["spec", "definition_of_terms", "failure_modes"],
    }


TAXONOMY_SCHEMA = taxonomy_schema(min_failure_modes=MIN_TAXONOMY_FAILURE_MODES)


async def run_taxonomy(
    *,
    spec: str,
    model: str,
    failure_mode_count: int = DEFAULT_FAILURE_MODE_COUNT,
    temperature: float | None = None,
    max_tokens: int | None = None,
    reasoning_effort: str | None = None,
    save_dir: str | None = None,
) -> dict[str, Any]:
    """Generate one taxonomy JSON artifact from the provided spec text."""
    if not spec:
        raise ValueError("spec text is required")

    spec_text = spec.strip()
    temperature = temperature if temperature is not None else DEFAULT_TAXONOMY_TEMPERATURE
    max_tokens = max_tokens if max_tokens is not None else DEFAULT_TAXONOMY_MAX_TOKENS
    # Reasoning models don't support temperature
    if reasoning_effort is not None:
        temperature = None
    save_path = Path(save_dir) if save_dir else Path("artifacts/outputs")
    system_prompt = GEN_PROMPT.replace("{{FAILURE_MODE_TARGET}}", str(failure_mode_count))
    messages = [
        Message(role="system", content=system_prompt),
        Message(role="user", content=spec_text),
    ]

    response = await generate_structured(
        model,
        messages,
        schema_name="taxonomy",
        json_schema=TAXONOMY_SCHEMA,
        options=GenerateOptions(
            temperature=temperature,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
        ),
    )
    taxonomy_json = response.parsed
    if not isinstance(taxonomy_json, dict):
        raise ValueError("taxonomy generation returned non-JSON output")

    save_path.mkdir(parents=True, exist_ok=True)
    taxonomy_path = save_path / "taxonomy.json"
    write_json(taxonomy_path, taxonomy_json)

    return {
        "taxonomy_path": str(taxonomy_path),
        "taxonomy": taxonomy_json,
    }


async def run(ctx: dict[str, Any], raw_cfg: dict[str, Any]) -> dict[str, Any]:
    """Validate config and run taxonomy generation via systematization."""
    if "validators" in raw_cfg or "validator_models" in raw_cfg:
        raise ValueError("taxonomy validators are no longer supported")

    model = raw_cfg.get("model")
    if not isinstance(model, dict):
        raise ValueError("taxonomy.model must be a mapping")
    model_cfg = parse_model_config(
        model,
        field_name="taxonomy.model",
        default_temperature=DEFAULT_TAXONOMY_TEMPERATURE,
        default_max_tokens=DEFAULT_TAXONOMY_MAX_TOKENS,
    )

    failure_mode_count = raw_cfg.get("failure_mode_count", DEFAULT_FAILURE_MODE_COUNT)
    if not isinstance(failure_mode_count, int) or failure_mode_count < 1:
        raise ValueError("taxonomy.failure_mode_count must be a positive integer")

    web_search_raw = raw_cfg.get("web_search")
    if web_search_raw is not None and not isinstance(web_search_raw, bool):
        raise ValueError("taxonomy.web_search must be a boolean")
    web_search = web_search_raw if web_search_raw is not None else True

    suite_root = Path(ctx["suite_root"])
    save_dir = raw_cfg.get("save_dir") or str(suite_root)

    cfg = resolve_stage_paths(
        {"save_dir": save_dir},
        cfg_path=ctx["config_path"],
        artifacts_root=ctx["artifacts_root"],
    )

    spec_name = ctx.get("spec_name") or "spec"
    spec_text = ctx.get("spec") or ""
    context = ctx.get("context")

    from p2m.core.config_model import ModelConfig as SysModelConfig
    from p2m.stages.systematization import run_systematization
    from p2m.stages.systematization_convert import run_systematization_to_taxonomy

    sys_model_cfg = SysModelConfig(
        name=model_cfg.name,
        temperature=model_cfg.temperature,
        max_tokens=model_cfg.max_tokens or DEFAULT_TAXONOMY_MAX_TOKENS,
        reasoning_effort=model_cfg.reasoning_effort,
    )
    sys_path = str(Path(cfg["save_dir"]) / "systematization.json")
    await run_systematization(
        spec=spec_name,
        spec_text=spec_text,
        save_path=sys_path,
        model_cfg=sys_model_cfg,
        web_search=web_search,
        context=context,
    )

    taxonomy_path_str = str(Path(cfg["save_dir"]) / "taxonomy.json")
    convert_model_cfg = SysModelConfig(
        name=model_cfg.name,
        temperature=model_cfg.temperature,
        max_tokens=model_cfg.max_tokens or DEFAULT_TAXONOMY_MAX_TOKENS,
        reasoning_effort=model_cfg.reasoning_effort,
    )
    await run_systematization_to_taxonomy(
        systematization_path=sys_path,
        save_path=taxonomy_path_str,
        model_cfg=convert_model_cfg,
        failure_mode_count_hint=failure_mode_count,
    )

    return {
        "taxonomy_dir": cfg["save_dir"],
        "taxonomy_path": taxonomy_path_str,
    }
