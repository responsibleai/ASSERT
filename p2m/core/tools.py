"""Target tool conversion helpers."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List

import yaml


def _normalize_parameter_schema(param: Dict[str, Any]) -> dict[str, Any]:
    param_type = param.get("type", "string")
    schema: dict[str, Any] = {
        "type": param_type,
        "description": param.get("description", ""),
    }
    if param_type == "array":
        schema["items"] = {"type": "string"}
    return schema


def normalize_tool_def(tool_def: Dict[str, Any]) -> dict[str, Any]:
    if "input_schema" in tool_def:
        schema = deepcopy(tool_def["input_schema"])
        if not isinstance(schema, dict):
            raise ValueError("tool input_schema must be a mapping")
        if schema.get("type") != "object":
            raise ValueError("tool input_schema must be a JSON object schema")
        schema.setdefault("additionalProperties", False)
        schema.setdefault("properties", {})
        schema.setdefault("required", [])
        return {
            "name": tool_def["name"],
            "description": tool_def.get("description", ""),
            "input_schema": schema,
        }

    props: dict[str, Any] = {}
    required: list[str] = []
    for param in tool_def.get("parameters", []):
        if not isinstance(param, dict) or "name" not in param:
            raise ValueError("tool parameters must be objects with a 'name'")
        props[param["name"]] = _normalize_parameter_schema(param)
        required.append(param["name"])
    return {
        "name": tool_def["name"],
        "description": tool_def.get("description", ""),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": props,
            "required": required,
        },
    }


def normalize_tool_defs(item_tools: List[Dict[str, Any]]) -> list[dict[str, Any]]:
    return [normalize_tool_def(tool_def) for tool_def in item_tools]


def build_target_tools(item_tools: List[Dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert tool definitions into OpenAI-format tool dicts."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool_def["name"],
                "description": tool_def.get("description", ""),
                "parameters": tool_def["input_schema"],
            },
        }
        for tool_def in normalize_tool_defs(item_tools)
    ]


def load_toolset_file(path: str | Path) -> list[dict[str, Any]]:
    resolved = Path(path).expanduser()
    try:
        data = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise FileNotFoundError(f"Toolset file not found: {resolved}") from None
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in toolset file {resolved}: {exc}") from exc
    if isinstance(data, dict) and "tools" in data:
        data = data["tools"]
    if not isinstance(data, list):
        raise ValueError("toolset YAML must be a list or a mapping with a 'tools' list")
    return normalize_tool_defs(data)
