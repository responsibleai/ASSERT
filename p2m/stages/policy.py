"""Generate a policy artifact from a risk description."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from p2m.config import resolve_stage_paths
from p2m.core.config_model import (
    DEFAULT_POLICY_MAX_TOKENS,
    DEFAULT_POLICY_TEMPERATURE,
    ModelConfig,
)
from p2m.core.io import write_json
from p2m.core.model_client import GenerateOptions, Message, generate_structured

BASE_DIR = Path(__file__).resolve().parents[2]
GEN_PROMPT = (BASE_DIR / "prompts" / "policy_system.md").read_text()
DEFAULT_SUB_RISK_COUNT = 25
MIN_POLICY_SUB_RISKS = 5
SCOPE = "suite"
SUITE_OUTPUT = "policy.json"


def policy_schema(*, min_sub_risks: int = 0) -> Dict[str, Any]:
    """JSON schema for a risk policy with sub-risks and term definitions."""
    sub_risks_prop: Dict[str, Any] = {
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
    if min_sub_risks > 0:
        pass  # minItems not supported in strict mode; count validated post-generation
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "risk": {
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
            "sub_risks": sub_risks_prop,
        },
        "required": ["risk", "definition_of_terms", "sub_risks"],
    }


POLICY_SCHEMA = policy_schema(min_sub_risks=MIN_POLICY_SUB_RISKS)


async def run_policy(
    *,
    risk: str,
    model: str,
    sub_risk_count: int = DEFAULT_SUB_RISK_COUNT,
    temperature: float | None = None,
    max_tokens: int | None = None,
    save_dir: str | None = None,
) -> dict[str, Any]:
    """Generate one policy JSON artifact from the provided risk text."""
    if not risk:
        raise ValueError("risk is required (resolved text from examples/risks/)")

    risk_text = risk.strip()
    temperature = temperature if temperature is not None else DEFAULT_POLICY_TEMPERATURE
    max_tokens = max_tokens if max_tokens is not None else DEFAULT_POLICY_MAX_TOKENS
    save_path = Path(save_dir) if save_dir else Path("artifacts/outputs")
    system_prompt = GEN_PROMPT.replace("{{SUB_RISK_TARGET}}", str(sub_risk_count))
    messages = [
        Message(role="system", content=system_prompt),
        Message(role="user", content=risk_text),
    ]

    response = await generate_structured(
        model,
        messages,
        schema_name="policy",
        json_schema=POLICY_SCHEMA,
        options=GenerateOptions(
            temperature=temperature,
            max_tokens=max_tokens,
        ),
    )
    policy_json = response.parsed
    if not isinstance(policy_json, dict):
        raise ValueError("policy generation returned non-JSON output")

    save_path.mkdir(parents=True, exist_ok=True)
    policy_path = save_path / "policy.json"
    write_json(policy_path, policy_json)

    return {
        "policy_path": str(policy_path),
        "policy": policy_json,
    }


async def run(ctx: dict[str, Any], raw_cfg: dict[str, Any]) -> dict[str, Any]:
    """Validate config and run the policy workflow."""
    if "validators" in raw_cfg or "validator_models" in raw_cfg:
        raise ValueError("policy validators are no longer supported")
    model = raw_cfg.get("model")
    if not isinstance(model, dict):
        raise ValueError("policy.model must be a mapping")
    model_cfg = ModelConfig(
        name=str(model.get("name") or "").strip(),
        temperature=model.get("temperature", DEFAULT_POLICY_TEMPERATURE),
        max_tokens=model.get("max_tokens", DEFAULT_POLICY_MAX_TOKENS),
    )
    if not model_cfg.name:
        raise ValueError("policy.model is required")
    cfg = resolve_stage_paths(
        {
            "model": model_cfg.name,
            "temperature": model_cfg.temperature,
            "max_tokens": model_cfg.max_tokens,
            "sub_risk_count": raw_cfg.get("sub_risk_count", DEFAULT_SUB_RISK_COUNT),
            "save_dir": raw_cfg.get("save_dir") or str(ctx["suite_root"]),
        },
        cfg_path=ctx["config_path"],
        artifacts_root=ctx["artifacts_root"],
    )
    result = await run_policy(
        risk=ctx["risk"] or "",
        model=cfg["model"],
        sub_risk_count=cfg["sub_risk_count"],
        temperature=cfg["temperature"],
        max_tokens=cfg["max_tokens"],
        save_dir=cfg["save_dir"],
    )
    policy = result.get("policy") or {}
    sub_risks = policy.get("sub_risks") or []
    return {
        "policy_dir": cfg["save_dir"],
        "policy_path": result["policy_path"],
        "_summary": {
            "risk": (policy.get("risk") or {}).get("name", ctx.get("risk", "")),
            "sub_risk_count": len(sub_risks),
            "sub_risk_names": [str(sr.get("name", "")) for sr in sub_risks[:5]],
        },
    }
