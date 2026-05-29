# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Typed runtime config model derived from the canonical pipeline YAML."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

DEFAULT_SYSTEMATIZATION_MODEL = "azure/gpt-5.4"

DEFAULT_GENERATION_TEMPERATURE = None
DEFAULT_GENERATION_MAX_TOKENS = 3000
DEFAULT_SYSTEMATIZE_TEMPERATURE = None
DEFAULT_SYSTEMATIZE_MAX_TOKENS = 10000
DEFAULT_SYSTEMATIZATION_TEMPERATURE = None
DEFAULT_SYSTEMATIZATION_MAX_TOKENS = None  # uncapped; model uses its own limit
DEFAULT_SYSTEMATIZATION_CONVERT_TEMPERATURE = None
DEFAULT_SYSTEMATIZATION_CONVERT_MAX_TOKENS = None  # uncapped; model uses its own limit

DEFAULT_INFERENCE_MAX_TOOL_CALLS = 10
DEFAULT_INFERENCE_TEMPERATURE = None
DEFAULT_INFERENCE_MAX_TOKENS = 10000
# Default fan-out for the inference and judge stages. Overridable per-run via
# ``pipeline.inference.concurrency`` in YAML or the ``--concurrency`` CLI flag.
DEFAULT_INFERENCE_CONCURRENCY = 10
DEFAULT_TESTER_MAX_TURNS = 10
DEFAULT_JUDGE_TEMPERATURE = None
DEFAULT_JUDGE_MAX_TOKENS = 12000
DEFAULT_MODEL_TIMEOUT_S = 300.0  # 5 minutes per API call


def _normalize_optional_string(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("expected a string value")
    stripped = value.strip()
    return stripped or None


def _require_nonempty_string(value: str, *, field_name: str) -> str:
    normalized = _normalize_optional_string(value)
    if normalized is None:
        raise ValueError(f"{field_name} is required")
    return normalized


@dataclass
class ModelConfig:
    name: str
    temperature: float | None = None
    max_tokens: int | None = None
    reasoning_effort: str | None = None

    def __post_init__(self) -> None:
        self.name = _require_nonempty_string(self.name, field_name="model.name")
        if self.max_tokens is not None and self.max_tokens <= 0:
            raise ValueError("model.max_tokens must be > 0")
        if self.reasoning_effort is not None:
            if not isinstance(self.reasoning_effort, str):
                raise ValueError("model.reasoning_effort must be a string")
            self.reasoning_effort = self.reasoning_effort.strip()
            if not self.reasoning_effort:
                raise ValueError("model.reasoning_effort must be a non-empty string")

    def __eq__(self, other: object) -> bool:
        if isinstance(other, str):
            return self.name == other
        return super().__eq__(other)


@dataclass
class ToolsConfig:
    module: str | None = None
    toolset: str | None = None
    simulator: str | None = None

    def __post_init__(self) -> None:
        self.module = _normalize_optional_string(self.module)
        self.toolset = _normalize_optional_string(self.toolset)
        self.simulator = _normalize_optional_string(self.simulator)
        if not self.module and not self.toolset and not self.simulator:
            raise ValueError("target.tools must define module or toolset+simulator")
        if self.module and self.toolset:
            raise ValueError("target.tools.module and target.tools.toolset are mutually exclusive")
        if self.module and self.simulator:
            raise ValueError("target.tools.module and target.tools.simulator are mutually exclusive")
        if self.toolset and not self.simulator:
            raise ValueError("target.tools.toolset requires target.tools.simulator")


VALID_TRACE_GROUP_BY = ("session.id",)


@dataclass
class TraceConfig:
    backend: str = "phoenix"
    group_by: str = "session.id"

    def __post_init__(self) -> None:
        if self.group_by not in VALID_TRACE_GROUP_BY:
            raise ValueError(
                f"trace.group_by must be 'session.id'. "
                f"Trace-level and span-level evaluation (trace.id, span.id) are under development. "
                f"Please raise a GitHub issue for timeline and specific feature requests."
            )


@dataclass
class TargetConfig:
    model: ModelConfig | str | None = None
    system_prompt: str | None = None
    tools: ToolsConfig | None = None
    connector: str | None = None
    callable: str | None = None
    endpoint: str | None = None
    trace: TraceConfig | None = None

    def __post_init__(self) -> None:
        if isinstance(self.model, str):
            self.model = ModelConfig(name=self.model)
        self.system_prompt = _normalize_optional_string(self.system_prompt)
        self.connector = _normalize_optional_string(self.connector)
        has_model = bool(self.model)
        has_connector = bool(self.connector)
        has_callable = bool(self.callable)
        has_endpoint = bool(self.endpoint)
        count = sum([has_model, has_connector, has_callable, has_endpoint])
        if count != 1:
            raise ValueError(
                "target requires exactly one of 'model', 'connector', 'callable', or 'endpoint'"
            )
        if self.tools is not None and has_connector:
            raise ValueError("external target must not define target.tools")
        if self.tools is not None and has_callable:
            raise ValueError("callable target must not define target.tools")
        if self.tools is not None and has_endpoint:
            raise ValueError("endpoint target must not define target.tools")
        if self.tools is not None and not has_model:
            raise ValueError("target.tools requires target.model")

    @property
    def is_external(self) -> bool:
        return self.connector is not None

    @property
    def is_callable(self) -> bool:
        return self.callable is not None

    @property
    def is_endpoint(self) -> bool:
        return self.endpoint is not None


@dataclass
class InferenceConfig:
    max_tool_calls: int = DEFAULT_INFERENCE_MAX_TOOL_CALLS
    max_turns: int = DEFAULT_TESTER_MAX_TURNS
    tool_timeout_s: float | None = None
    startup_timeout_s: float | None = None
    concurrency: int = DEFAULT_INFERENCE_CONCURRENCY

    def __post_init__(self) -> None:
        if self.max_tool_calls <= 0:
            raise ValueError("inference.max_tool_calls must be > 0")
        if self.max_turns <= 0:
            raise ValueError("inference.max_turns must be > 0")
        if self.concurrency <= 0:
            raise ValueError("inference.concurrency must be > 0")
        if self.tool_timeout_s is not None and self.tool_timeout_s <= 0:
            raise ValueError("inference.tool_timeout_s must be > 0")
        if self.startup_timeout_s is not None and self.startup_timeout_s <= 0:
            raise ValueError("inference.startup_timeout_s must be > 0")


@dataclass
class TesterConfig:
    __test__ = False

    model: ModelConfig | str

    def __post_init__(self) -> None:
        if isinstance(self.model, str):
            self.model = ModelConfig(name=self.model)


@dataclass
class JudgeConfig:
    model: ModelConfig | str
    n: int = 1
    dimensions: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if isinstance(self.model, str):
            self.model = ModelConfig(name=self.model)
        if self.n <= 0:
            raise ValueError("judge.n must be > 0")
        if not isinstance(self.dimensions, list):
            raise ValueError("judge.dimensions must be a list")
        for dimension in self.dimensions:
            if not isinstance(dimension, dict):
                raise ValueError("judge.dimensions entries must be mappings")


@dataclass
class EvaluationConfig:
    judge: JudgeConfig | None = None
    tester: TesterConfig | None = None
    inference: InferenceConfig = field(default_factory=InferenceConfig)


@dataclass
class PipelineConfig:
    target: TargetConfig | None = None
    evaluation: EvaluationConfig | None = None


@dataclass
class SuiteMetadata:
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class RunManifest:
    started_at: str
    status: str = "running"
    ended_at: str | None = None
    stages: dict[str, str] = field(default_factory=dict)
    pid: int | None = None
    host: str | None = None
    heartbeat_at: str | None = None
    # Live progress payload (e.g. {"stage": "inference", "completed": 423,
    # "total": 1000}) updated by the ManifestHeartbeat during long stages
    # so external observers can see real-time progress without parsing
    # inference_set.jsonl. Cleared between stages and dropped from the
    # serialized output when None.
    progress: dict[str, Any] | None = None
    artifact_versions: dict[str, dict[str, Any]] = field(default_factory=dict)
    stage_timings: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        empty_collection_keys = {"artifact_versions", "stage_timings"}
        return {
            k: v
            for k, v in asdict(self).items()
            if v is not None and not (k in empty_collection_keys and not v)
        }
