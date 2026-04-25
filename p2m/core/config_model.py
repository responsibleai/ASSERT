"""Typed runtime config model derived from the canonical pipeline YAML."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

DEFAULT_SYSTEMATIZATION_MODEL = "azure/gpt-5.4"

DEFAULT_GENERATION_TEMPERATURE = 1.0
DEFAULT_GENERATION_MAX_TOKENS = 3000
DEFAULT_POLICY_TEMPERATURE = 1.0
DEFAULT_POLICY_MAX_TOKENS = 10000
DEFAULT_SYSTEMATIZATION_TEMPERATURE = 0.3
DEFAULT_SYSTEMATIZATION_MAX_TOKENS = None  # uncapped; model uses its own limit
DEFAULT_SYSTEMATIZATION_CONVERT_TEMPERATURE = 0.0
DEFAULT_SYSTEMATIZATION_CONVERT_MAX_TOKENS = None  # uncapped; model uses its own limit

DEFAULT_ROLLOUT_MAX_TOOL_CALLS = 10
DEFAULT_ROLLOUT_TEMPERATURE = 0.0
DEFAULT_ROLLOUT_MAX_TOKENS = 10000
DEFAULT_ROLLOUT_CONCURRENCY = 10
DEFAULT_AUDITOR_MAX_TURNS = 10
DEFAULT_JUDGE_TEMPERATURE = 0.0
DEFAULT_JUDGE_MAX_TOKENS = 12000


@dataclass
class ModelConfig:
    name: str
    temperature: float | None = None
    max_tokens: int | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("model.name is required")
        if self.max_tokens is not None and self.max_tokens <= 0:
            raise ValueError("model.max_tokens must be > 0")

    def __eq__(self, other: object) -> bool:
        if isinstance(other, str):
            return self.name == other
        return super().__eq__(other)

    def __str__(self) -> str:
        return self.name


@dataclass
class ToolsConfig:
    module: str | None = None
    toolset: str | None = None
    simulator: str | None = None

    def __post_init__(self) -> None:
        if not self.module and not self.toolset and not self.simulator:
            raise ValueError("target.tools must define module or toolset+simulator")
        if self.module and self.toolset:
            raise ValueError("target.tools.module and target.tools.toolset are mutually exclusive")
        if self.module and self.simulator:
            raise ValueError("target.tools.module and target.tools.simulator are mutually exclusive")
        if self.toolset and not self.simulator:
            raise ValueError("target.tools.toolset requires target.tools.simulator")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
class RolloutConfig:
    max_tool_calls: int = DEFAULT_ROLLOUT_MAX_TOOL_CALLS
    max_turns: int = DEFAULT_AUDITOR_MAX_TURNS
    tool_timeout_s: float | None = None
    startup_timeout_s: float | None = None
    concurrency: int = DEFAULT_ROLLOUT_CONCURRENCY

    def __post_init__(self) -> None:
        if self.max_tool_calls <= 0:
            raise ValueError("rollout.max_tool_calls must be > 0")
        if self.max_turns <= 0:
            raise ValueError("rollout.max_turns must be > 0")
        if self.concurrency <= 0:
            raise ValueError("rollout.concurrency must be > 0")
        if self.tool_timeout_s is not None and self.tool_timeout_s <= 0:
            raise ValueError("rollout.tool_timeout_s must be > 0")
        if self.startup_timeout_s is not None and self.startup_timeout_s <= 0:
            raise ValueError("rollout.startup_timeout_s must be > 0")


@dataclass
class AuditorConfig:
    model: ModelConfig | str

    def __post_init__(self) -> None:
        if isinstance(self.model, str):
            self.model = ModelConfig(name=self.model)
        if not self.model.name:
            raise ValueError("auditor.model.name is required")


@dataclass
class JudgeConfig:
    model: ModelConfig | str
    n: int = 1
    dimensions: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if isinstance(self.model, str):
            self.model = ModelConfig(name=self.model)
        if not self.model.name:
            raise ValueError("judge.model.name is required")
        if self.n <= 0:
            raise ValueError("judge.n must be > 0")


@dataclass
class EvaluationConfig:
    judge: JudgeConfig | None = None
    auditor: AuditorConfig | None = None
    rollout: RolloutConfig = field(default_factory=RolloutConfig)


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

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}
