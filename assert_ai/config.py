# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Load YAML configs and build the minimal runtime context for ASSERT."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import logging
import re
import yaml

log = logging.getLogger(__name__)

from assert_ai.core.config_model import (
    DEFAULT_INFERENCE_CONCURRENCY,
    DEFAULT_INFERENCE_MAX_TOOL_CALLS,
    DEFAULT_INFERENCE_MAX_TOKENS,
    DEFAULT_INFERENCE_TEMPERATURE,
    DEFAULT_JUDGE_MAX_TOKENS,
    DEFAULT_JUDGE_TEMPERATURE,
    DEFAULT_TESTER_MAX_TURNS,
    EndpointConfig,
    EvaluationConfig,
    JudgeConfig,
    ModelConfig,
    PipelineConfig,
    InferenceConfig,
    TargetConfig,
    TesterConfig,
    ToolsConfig,
    TraceConfig,
)

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH_KEYS = {"save_dir", "save_path"}
PIPELINE_STAGE_ORDER = (
    "systematize",
    "test_set",
    "inference",
    "judge",
)
BEHAVIOR_REQUIRED_PIPELINE_STAGES = {"systematize"}


class ConfigError(Exception):
    pass


# Must match the viewer's SAFE_ID_RE in artifacts.ts: /^[a-z0-9][a-z0-9._-]*$/i
_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")


def _validate_identifier(value: str, field_name: str) -> str:
    """Reject identifiers that don't match the safe slug format.

    Uses the same allowlist as the viewer's isSafeArtifactId to ensure
    IDs created by the pipeline are always readable by the viewer.
    """
    if not value:
        raise ConfigError(f"{field_name} must not be empty")
    if len(value) > 255:
        raise ConfigError(f"{field_name} exceeds maximum length of 255 characters")
    if ".." in value:
        raise ConfigError(f"{field_name} must not contain '..'")
    if not _SAFE_ID_RE.match(value):
        raise ConfigError(
            f"{field_name} must start with an alphanumeric character and contain only "
            f"alphanumerics, dots, hyphens, or underscores; got: {value!r}"
        )
    return value


def _require_within(child: Path, parent: Path, label: str) -> None:
    """Verify that a resolved path is inside the expected parent directory."""
    try:
        child.relative_to(parent)
    except ValueError:
        raise ConfigError(f"{label} escapes its expected root directory")


def require(condition: bool, message: str) -> None:
    """Raise a config error when a validation condition fails."""
    if not condition:
        raise ConfigError(message)


def load_config(cfg_path: Path) -> dict[str, Any]:
    """Load one YAML config file and require a mapping at the top level."""
    try:
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"Config file not found: {cfg_path}") from None
    except PermissionError as exc:
        raise ConfigError(f"Permission denied reading config file: {cfg_path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in config file {cfg_path}: {exc}") from exc
    require(isinstance(data, dict), "Top-level YAML must be a mapping")
    return data


def _strip_artifact_root_prefix(path: Path, artifacts_root: Path) -> Path | None:
    """Return the artifact-relative suffix for paths starting with artifacts roots."""
    parts = path.parts
    if not parts:
        return None
    if parts[0] not in {"artifacts", artifacts_root.name}:
        return None
    if len(parts) == 1:
        return Path()
    return Path(*parts[1:])


def _resolve_path(
    path: str | Path,
    *,
    artifacts_root: Path,
    cfg_dir: Path | None = None,
    use_artifacts_root: bool = False,
) -> str:
    """Resolve one path against artifacts and config roots.

    Validates that relative paths do not escape their expected root directory
    via traversal sequences.
    """
    artifacts_root = Path(artifacts_root).expanduser().resolve()
    cfg_dir = Path(cfg_dir).expanduser().resolve() if cfg_dir is not None else None
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        # Absolute paths are explicitly specified by the user; allow them.
        # The _validate_identifier and _require_within checks on suite_root/run_root
        # already protect against path traversal in identifiers.
        return str(candidate.resolve())
    artifact_relative = _strip_artifact_root_prefix(candidate, artifacts_root)
    if artifact_relative is not None:
        resolved = (artifacts_root / artifact_relative).resolve()
        _require_within(resolved, artifacts_root, f"artifact path '{path}'")
        return str(resolved)
    primary_root = artifacts_root if use_artifacts_root or cfg_dir is None else cfg_dir
    resolved = (primary_root / candidate).resolve()
    _require_within(resolved, primary_root, f"resolved path '{path}'")
    return str(resolved)


def _validate_pipeline_stages(
    pipeline_raw: dict[str, Any],
    *,
    stage_modules: dict[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    """Validate the pipeline mapping and return stages in canonical order."""
    unknown_stages = sorted(set(pipeline_raw).difference(stage_modules))
    require(not unknown_stages, f"Unknown stage(s): {', '.join(unknown_stages)}")

    stages: list[tuple[str, dict[str, Any]]] = []
    for stage_name in PIPELINE_STAGE_ORDER:
        if stage_name not in pipeline_raw:
            continue
        stage_cfg = pipeline_raw[stage_name]
        require(isinstance(stage_cfg, dict), f"pipeline.{stage_name} must be a mapping")
        stages.append((stage_name, stage_cfg))

    require(stages, "'pipeline' must define at least one stage")
    return stages


def load_runtime_context(
    raw: dict[str, Any],
    cfg_path: Path,
    *,
    stage_modules: dict[str, Any],
) -> dict[str, Any]:
    """Build the shared runtime context used by every stage."""
    reject_unknown_keys(
        raw,
        field_name="config",
        allowed={
            "suite",
            "run",
            "behavior",
            "context",
            "default_model",
            "artifacts_root",
            "results_dir",
            "pipeline",
        },
    )
    default_model_raw = _get_default_model_mapping(raw)
    pipeline_raw = raw.get("pipeline")
    require(isinstance(pipeline_raw, dict), "'pipeline' must be a mapping")
    dimensions = _parse_test_set_dimensions(pipeline_raw)
    if dimensions and any("levels" not in dimension for dimension in dimensions) and default_model_raw is None:
        raise ValueError(
            "default_model is required when test_set.stratify.dimensions use generated mode "
            "(description without levels)"
        )
    pipeline = parse_pipeline_config(raw)
    target = pipeline.target if pipeline else None

    artifacts_root = Path(raw.get("artifacts_root") or "artifacts").expanduser()
    if not artifacts_root.is_absolute():
        artifacts_root = (ROOT / artifacts_root).resolve()
    else:
        artifacts_root = artifacts_root.resolve()

    results_dir_raw = raw.get("results_dir")
    if results_dir_raw:
        results_dir = Path(
            _resolve_path(
                results_dir_raw,
                artifacts_root=artifacts_root,
                use_artifacts_root=True,
            )
        )
    else:
        results_dir = (artifacts_root / "results").resolve()

    suite_id = str(raw.get("suite") or datetime.now(timezone.utc).strftime("eval-%Y%m%dT%H%M%S"))
    _validate_identifier(suite_id, "suite")
    stages = _validate_pipeline_stages(pipeline_raw, stage_modules=stage_modules)
    if default_model_raw is not None:
        for stage_name, stage_cfg in stages:
            if stage_name in {"systematize", "test_set"} and "model" not in stage_cfg:
                stage_cfg["model"] = dict(default_model_raw)
            if stage_name == "test_set":
                stratify_cfg = stage_cfg.get("stratify")
                if isinstance(stratify_cfg, dict) and "model" not in stratify_cfg:
                    stratify_cfg["model"] = dict(default_model_raw)
    enabled_stage_names = [
        stage_name
        for stage_name, stage_cfg in stages
        if stage_cfg.get("enabled", True)
    ]

    has_enabled_run_stage = any(stage_modules[name].SCOPE == "run" for name in enabled_stage_names)
    requires_behavior = any(stage in BEHAVIOR_REQUIRED_PIPELINE_STAGES for stage in enabled_stage_names)

    run_id = raw.get("run")
    if has_enabled_run_stage and not run_id:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    run_id = str(run_id) if run_id else None
    if run_id is not None:
        _validate_identifier(run_id, "run")

    behavior_name = None
    behavior_description = ""
    behavior_raw = raw.get("behavior")
    if behavior_raw is None:
        require(not requires_behavior, "behavior is required when a behavior-backed stage is enabled")
    else:
        if not isinstance(behavior_raw, dict):
            raise ValueError("behavior must be a mapping")
        reject_unknown_keys(
            behavior_raw,
            field_name="behavior",
            allowed={"name", "description", "preset"},
        )
        preset_name = _optional_str(behavior_raw.get("preset"), field_name="behavior.preset")
        if preset_name:
            from assert_ai.library.loader import load_preset
            preset = load_preset("behavior", preset_name)
            if not behavior_raw.get("name"):
                behavior_raw = {**behavior_raw, "name": preset["name"]}
            if not behavior_raw.get("description"):
                behavior_raw = {**behavior_raw, "description": preset.get("description", "")}
        behavior_name = _optional_str(behavior_raw.get("name"), field_name="behavior.name")
        if not behavior_name:
            raise ValueError("behavior.name is required")
        _validate_identifier(behavior_name, "behavior.name")
        behavior_description = _optional_str(
            behavior_raw.get("description"),
            field_name="behavior.description",
        ) or ""
        require(
            bool(behavior_description) or not requires_behavior,
            "behavior.description is required when a behavior-backed stage is enabled",
        )

    context = raw.get("context")
    if context is not None and not isinstance(context, str):
        raise ValueError("context must be a string")

    suite_root = (results_dir / suite_id).resolve()
    _require_within(suite_root, results_dir, "suite_root")
    run_root = (suite_root / run_id).resolve() if run_id else None
    if run_root is not None:
        _require_within(run_root, suite_root, "run_root")

    return {
        "config_path": cfg_path,
        "suite_id": suite_id,
        "run_id": run_id,
        "behavior_name": behavior_name,
        "behavior": behavior_description,
        "context": context,
        "dimensions": dimensions,
        "artifacts_root": artifacts_root,
        "results_dir": results_dir,
        "suite_root": suite_root,
        "run_root": run_root,
        "stages": stages,
        "target": target,
        "evaluation": pipeline.evaluation if pipeline else None,
    }


def resolve_stage_paths(
    cfg: dict[str, Any],
    *,
    cfg_path: Path,
    artifacts_root: Path,
) -> dict[str, Any]:
    """Resolve all *_path and *_dir values in one stage config mapping."""
    resolved = dict(cfg)
    for key, value in list(resolved.items()):
        if not value or not key.endswith(("_path", "_dir")):
            continue
        resolved[key] = _resolve_path(
            value,
            artifacts_root=artifacts_root,
            cfg_dir=cfg_path.parent,
            use_artifacts_root=key in OUTPUT_PATH_KEYS,
        )
    return resolved


# ── Config parsing ─────────────────────────────────────────────


def _optional_str(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    stripped = value.strip()
    return stripped or None


def _parse_preset_names(value: Any, *, field_name: str) -> list[str]:
    """Accept a single preset name (str) or a list of preset names.

    Returns a list of stripped, non-empty names preserving order. Duplicates are
    removed (first occurrence wins) so callers can rely on deterministic
    merge order. Returns [] when value is None or empty.
    """
    if value is None:
        return []
    if isinstance(value, str):
        name = value.strip()
        return [name] if name else []
    if isinstance(value, list):
        names: list[str] = []
        seen: set[str] = set()
        for index, item in enumerate(value):
            if not isinstance(item, str):
                raise ValueError(f"{field_name}[{index}] must be a string")
            stripped = item.strip()
            if not stripped:
                raise ValueError(f"{field_name}[{index}] must not be empty")
            if stripped in seen:
                continue
            seen.add(stripped)
            names.append(stripped)
        return names
    raise ValueError(f"{field_name} must be a string or a list of strings")


def _optional_float(value: Any, *, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError as exc:
            raise ValueError(f"{field_name} must be numeric") from exc
    raise ValueError(f"{field_name} must be numeric")


def _optional_int(value: Any, *, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError as exc:
            raise ValueError(f"{field_name} must be an integer") from exc
    raise ValueError(f"{field_name} must be an integer")


def _coalesce(value: Any, default: Any) -> Any:
    """Return *value* unless it is ``None``, in which case return *default*.

    Unlike ``value or default``, this preserves falsy values like ``0``.
    """
    return value if value is not None else default


def reject_unknown_keys(raw: dict[str, Any], *, field_name: str, allowed: set[str]) -> None:
    unknown = sorted(set(raw).difference(allowed))
    if unknown:
        raise ValueError(f"{field_name} has unsupported field(s): {', '.join(unknown)}")


def parse_model_config(
    raw: Any,
    *,
    field_name: str,
    default_temperature: float | None = None,
    default_max_tokens: int | None = None,
) -> ModelConfig:
    if not isinstance(raw, dict):
        raise ValueError(f"{field_name} must be a mapping")
    reject_unknown_keys(
        raw,
        field_name=field_name,
        allowed={"name", "temperature", "max_tokens", "reasoning_effort"},
    )
    name = _optional_str(raw.get("name"), field_name=f"{field_name}.name")
    if not name:
        raise ValueError(f"{field_name}.name is required")
    temperature = _optional_float(
        raw.get("temperature"),
        field_name=f"{field_name}.temperature",
    )
    max_tokens = _optional_int(
        raw.get("max_tokens"),
        field_name=f"{field_name}.max_tokens",
    )
    reasoning_effort_raw = raw.get("reasoning_effort")
    reasoning_effort = _optional_str(
        reasoning_effort_raw,
        field_name=f"{field_name}.reasoning_effort",
    )
    if "reasoning_effort" in raw and reasoning_effort_raw is not None and reasoning_effort is None:
        raise ValueError(f"{field_name}.reasoning_effort must be a non-empty string")
    return ModelConfig(
        name=name,
        temperature=temperature if temperature is not None else default_temperature,
        max_tokens=max_tokens if max_tokens is not None else default_max_tokens,
        reasoning_effort=reasoning_effort,
    )


def parse_tools_config(raw: dict[str, Any], *, field_name: str) -> ToolsConfig:
    if not isinstance(raw, dict):
        raise ValueError(f"{field_name} must be a mapping")
    reject_unknown_keys(raw, field_name=field_name, allowed={"module", "toolset", "simulator"})
    return ToolsConfig(
        module=_optional_str(raw.get("module"), field_name=f"{field_name}.module"),
        toolset=_optional_str(raw.get("toolset"), field_name=f"{field_name}.toolset"),
        simulator=_optional_str(raw.get("simulator"), field_name=f"{field_name}.simulator"),
    )


def parse_endpoint_config(raw: Any, *, field_name: str) -> EndpointConfig | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        url = _optional_str(raw, field_name=field_name)
        return EndpointConfig(url=url or "")
    if not isinstance(raw, dict):
        raise ValueError(f"{field_name} must be a string or mapping")
    reject_unknown_keys(raw, field_name=field_name, allowed={"url", "protocol", "model", "api_key_env", "stream"})
    url = _optional_str(raw.get("url"), field_name=f"{field_name}.url")
    if not url:
        raise ValueError(f"{field_name}.url is required")
    return EndpointConfig(
        url=url,
        protocol=_optional_str(raw.get("protocol"), field_name=f"{field_name}.protocol") or "assert",
        model=_optional_str(raw.get("model"), field_name=f"{field_name}.model"),
        api_key_env=_optional_str(raw.get("api_key_env"), field_name=f"{field_name}.api_key_env"),
        stream=raw.get("stream", False),
    )


def parse_target_config(raw: dict[str, Any], *, field_name: str) -> TargetConfig:
    if not isinstance(raw, dict):
        raise ValueError(f"{field_name} must be a mapping")
    reject_unknown_keys(raw, field_name=field_name, allowed={"model", "system_prompt", "tools", "connector", "callable", "endpoint", "trace"})
    tools_raw = raw.get("tools")
    tools = None
    if tools_raw is not None:
        tools = parse_tools_config(tools_raw, field_name=f"{field_name}.tools")
    trace_raw = raw.get("trace")
    trace = None
    if trace_raw is not None:
        if not isinstance(trace_raw, dict):
            raise ValueError(f"{field_name}.trace must be a mapping")
        trace = TraceConfig(
            backend=trace_raw.get("backend", "phoenix"),
            group_by=trace_raw.get("group_by", "session.id"),
        )
    return TargetConfig(
        model=(
            parse_model_config(
                raw.get("model"),
                field_name=f"{field_name}.model",
                default_temperature=DEFAULT_INFERENCE_TEMPERATURE,
                default_max_tokens=DEFAULT_INFERENCE_MAX_TOKENS,
            )
            if raw.get("model") is not None
            else None
        ),
        system_prompt=_optional_str(raw.get("system_prompt"), field_name=f"{field_name}.system_prompt"),
        tools=tools,
        connector=_optional_str(raw.get("connector"), field_name=f"{field_name}.connector"),
        callable=_optional_str(raw.get("callable"), field_name=f"{field_name}.callable"),
        endpoint=parse_endpoint_config(raw.get("endpoint"), field_name=f"{field_name}.endpoint"),
        trace=trace,
    )


def _get_default_model_mapping(raw: dict[str, Any]) -> dict[str, Any] | None:
    default_model_raw = raw.get("default_model")
    if default_model_raw is None:
        return None
    if not isinstance(default_model_raw, dict):
        raise ValueError("default_model must be a mapping")
    parse_model_config(default_model_raw, field_name="default_model")
    return dict(default_model_raw)


def _parse_test_set_dimensions(pipeline_raw: dict[str, Any]) -> list[dict[str, Any]] | None:
    test_set_stage = pipeline_raw.get("test_set")
    if test_set_stage is None:
        return None
    if not isinstance(test_set_stage, dict):
        raise ValueError("pipeline.test_set must be a mapping")
    stratify_raw = test_set_stage.get("stratify")
    if stratify_raw is None:
        return None
    if not isinstance(stratify_raw, dict):
        raise ValueError("pipeline.test_set.stratify must be a mapping")
    reject_unknown_keys(
        stratify_raw,
        field_name="pipeline.test_set.stratify",
        allowed={"dimensions", "level_count", "model"},
    )
    return _parse_dimensions(stratify_raw.get("dimensions"))


def _parse_dimensions(raw: Any) -> list[dict[str, Any]] | None:
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ValueError("dimensions must be a list")
    if len(raw) > 10:
        log.warning("dimensions defines more than 10 dimensions")

    dimensions: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    saw_explicit_levels = False
    saw_generated_levels = False
    for index, dimension_raw in enumerate(raw, start=1):
        field_name = f"dimensions[{index}]"
        if not isinstance(dimension_raw, dict):
            raise ValueError(f"{field_name} must be a mapping")
        reject_unknown_keys(
            dimension_raw,
            field_name=field_name,
            allowed={"name", "description", "levels"},
        )
        name = _optional_str(dimension_raw.get("name"), field_name=f"{field_name}.name")
        if not name:
            raise ValueError(f"{field_name}.name is required")
        if name == "behavior":
            raise ValueError("dimension name 'behavior' is reserved")
        if name in seen_names:
            raise ValueError(f"duplicate dimension name: {name}")
        seen_names.add(name)

        description = _optional_str(
            dimension_raw.get("description"),
            field_name=f"{field_name}.description",
        )
        levels_raw = dimension_raw.get("levels")
        levels = None
        if levels_raw is not None:
            if not isinstance(levels_raw, list) or not levels_raw:
                raise ValueError(f"{field_name}.levels must be a non-empty list")
            if len(levels_raw) == 1:
                raise ValueError("single-level dimension adds no variation")
            if len(levels_raw) > 20:
                log.warning(f"dimension '{name}' defines more than 20 levels")
            levels = []
            seen_level_names: set[str] = set()
            for level_index, level_raw in enumerate(levels_raw, start=1):
                level_field_name = f"{field_name}.levels[{level_index}]"
                if not isinstance(level_raw, dict):
                    raise ValueError(f"{level_field_name} must be a mapping")
                reject_unknown_keys(
                    level_raw,
                    field_name=level_field_name,
                    allowed={"name", "definition"},
                )
                level_name = _optional_str(
                    level_raw.get("name"),
                    field_name=f"{level_field_name}.name",
                )
                definition = _optional_str(
                    level_raw.get("definition"),
                    field_name=f"{level_field_name}.definition",
                )
                if not level_name:
                    raise ValueError(f"{level_field_name}.name is required")
                if not definition:
                    raise ValueError(f"{level_field_name}.definition is required")
                if level_name in seen_level_names:
                    raise ValueError(f"duplicate level name in {name}: {level_name}")
                seen_level_names.add(level_name)
                levels.append({"name": level_name, "definition": definition})
            saw_explicit_levels = True
        else:
            saw_generated_levels = True

        if levels is None and description is None:
            raise ValueError(f"{field_name} must define either levels or description")

        dimension: dict[str, Any] = {"name": name}
        if description is not None:
            dimension["description"] = description
        if levels is not None:
            dimension["levels"] = levels
        dimensions.append(dimension)

    if saw_explicit_levels and saw_generated_levels:
        raise ValueError(
            "all dimensions must use the same mode: either all with explicit levels or all with descriptions for generation"
        )
    return dimensions


def parse_judge_dimensions(raw: Any, *, field_name: str) -> list[dict[str, Any]]:
    if raw in (None, {}):
        return []
    if not isinstance(raw, dict):
        raise ValueError(f"{field_name} must be a mapping")

    dimensions: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for raw_name, dimension_raw in raw.items():
        name = _optional_str(raw_name, field_name=f"{field_name}.<name>")
        if not name:
            raise ValueError(f"{field_name} keys must be non-empty strings")
        if name in seen_names:
            raise ValueError(f"duplicate judge dimension name: {name}")
        seen_names.add(name)
        dimension_field_name = f"{field_name}.{name}"
        if not isinstance(dimension_raw, dict):
            raise ValueError(f"{dimension_field_name} must be a mapping")
        reject_unknown_keys(
            dimension_raw,
            field_name=dimension_field_name,
            allowed={"description", "rubric", "required_base"},
        )
        description = _optional_str(
            dimension_raw.get("description"),
            field_name=f"{dimension_field_name}.description",
        )
        rubric = _optional_str(
            dimension_raw.get("rubric"),
            field_name=f"{dimension_field_name}.rubric",
        )
        if not description:
            raise ValueError(f"{dimension_field_name}.description is required")
        if not rubric:
            raise ValueError(f"{dimension_field_name}.rubric is required")
        required_base = dimension_raw.get("required_base")
        if required_base is not None and not isinstance(required_base, bool):
            raise ValueError(f"{dimension_field_name}.required_base must be a boolean")

        dimension = {
            "name": name,
            "description": description,
            "rubric": rubric,
        }
        if required_base is not None:
            dimension["required_base"] = required_base
        dimensions.append(dimension)
    return dimensions


def parse_pipeline_config(raw: dict[str, Any]) -> PipelineConfig | None:
    pipeline_raw = raw.get("pipeline")
    if pipeline_raw is None:
        return None
    if not isinstance(pipeline_raw, dict):
        raise ValueError("pipeline must be a mapping")

    default_model_raw = _get_default_model_mapping(raw)
    inference_stage = pipeline_raw.get("inference")
    scorer_stage = pipeline_raw.get("judge")

    if inference_stage is not None and not isinstance(inference_stage, dict):
        raise ValueError("pipeline.inference must be a mapping")
    if scorer_stage is not None and not isinstance(scorer_stage, dict):
        raise ValueError("pipeline.judge must be a mapping")

    target = None
    inference_cfg = InferenceConfig()
    tester = None
    judge = None
    inference_enabled = inference_stage is not None and bool(inference_stage.get("enabled", True))
    judge_enabled = scorer_stage is not None and bool(scorer_stage.get("enabled", True))

    if inference_stage is not None:
        reject_unknown_keys(
            inference_stage,
            field_name="pipeline.inference",
            allowed={"target", "tester", "max_turns", "max_tool_calls",
                      "tool_timeout_s", "startup_timeout_s", "concurrency",
                      "test_set_path", "save_dir", "strict", "enabled", "file_path"},
        )
        if inference_enabled:
            target_raw = inference_stage.get("target")
            require(target_raw is not None, "pipeline.inference.target is required when inference stage is enabled")
            if not isinstance(target_raw, dict):
                raise ValueError("pipeline.inference.target must be a mapping")
            target_raw = dict(target_raw)
            if "model" not in target_raw and "connector" not in target_raw and "callable" not in target_raw and "endpoint" not in target_raw and default_model_raw is not None:
                target_raw["model"] = dict(default_model_raw)
            target = parse_target_config(target_raw, field_name="pipeline.inference.target")

            tester_raw = inference_stage.get("tester")
            if tester_raw is not None:
                if not isinstance(tester_raw, dict):
                    raise ValueError("pipeline.inference.tester must be a mapping")
                if "max_turns" in tester_raw:
                    raise ValueError("pipeline.inference.tester.max_turns is no longer supported; use pipeline.inference.max_turns")
                tester_model_raw = tester_raw.get("model", default_model_raw)
                require(
                    tester_model_raw is not None,
                    "pipeline.inference.tester.model or default_model is required when inference.tester is configured",
                )
                tester = TesterConfig(
                    model=parse_model_config(
                        tester_model_raw,
                        field_name="pipeline.inference.tester.model",
                        default_temperature=DEFAULT_INFERENCE_TEMPERATURE,
                        default_max_tokens=DEFAULT_INFERENCE_MAX_TOKENS,
                    ),
                )

            inference_cfg = InferenceConfig(
                max_tool_calls=_coalesce(_optional_int(
                    inference_stage.get("max_tool_calls"),
                    field_name="pipeline.inference.max_tool_calls",
                ), DEFAULT_INFERENCE_MAX_TOOL_CALLS),
                max_turns=_coalesce(_optional_int(
                    inference_stage.get("max_turns"),
                    field_name="pipeline.inference.max_turns",
                ), DEFAULT_TESTER_MAX_TURNS),
                tool_timeout_s=_optional_float(
                    inference_stage.get("tool_timeout_s"),
                    field_name="pipeline.inference.tool_timeout_s",
                ),
                startup_timeout_s=_optional_float(
                    inference_stage.get("startup_timeout_s"),
                    field_name="pipeline.inference.startup_timeout_s",
                ),
                concurrency=_coalesce(_optional_int(
                    inference_stage.get("concurrency"),
                    field_name="pipeline.inference.concurrency",
                ), DEFAULT_INFERENCE_CONCURRENCY),
            )

    if scorer_stage is not None:
        reject_unknown_keys(
            scorer_stage,
            field_name="pipeline.judge",
            allowed={"model", "n", "dimensions", "inference_set_path", "taxonomy_path", "save_dir",
                       "enabled", "file_path", "preset"},
        )
        if judge_enabled:
            model_raw = scorer_stage.get("model", default_model_raw)
            require(model_raw is not None, "pipeline.judge.model or default_model is required when judge is configured")
            judge_preset_names = _parse_preset_names(
                scorer_stage.get("preset"),
                field_name="pipeline.judge.preset",
            )
            preset_dims: list[dict[str, Any]] = []
            if judge_preset_names:
                from assert_ai.library.loader import load_preset
                # Later presets override earlier ones on dimension-name conflict.
                merged: dict[str, dict[str, Any]] = {}
                for preset_name in judge_preset_names:
                    preset = load_preset("judge_preset", preset_name)
                    for dim in parse_judge_dimensions(
                        preset.get("dimensions"),
                        field_name=f"pipeline.judge.preset({preset_name}).dimensions",
                    ):
                        merged[dim["name"]] = dim
                preset_dims = list(merged.values())
            inline_dims = parse_judge_dimensions(
                scorer_stage.get("dimensions"),
                field_name="pipeline.judge.dimensions",
            )
            inline_names = {d["name"] for d in inline_dims}
            dimensions = [d for d in preset_dims if d["name"] not in inline_names] + inline_dims
            judge = JudgeConfig(
                model=parse_model_config(
                    model_raw,
                    field_name="pipeline.judge.model",
                    default_temperature=DEFAULT_JUDGE_TEMPERATURE,
                    default_max_tokens=DEFAULT_JUDGE_MAX_TOKENS,
                ),
                n=_coalesce(_optional_int(scorer_stage.get("n"), field_name="pipeline.judge.n"), 1),
                dimensions=dimensions,
            )

    evaluation = None
    if judge is not None or inference_enabled:
        evaluation = EvaluationConfig(judge=judge, tester=tester, inference=inference_cfg)

    return PipelineConfig(target=target, evaluation=evaluation)
