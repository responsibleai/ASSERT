# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""One-call setup for an ASSERT run: target + policy + mocks.

The setup flow reads, in order: point ASSERT at a container or endpoint, declare
the tool calls and scenarios to mock in a separate modular YAML, start the
sandbox, and return eval results.

This module owns the first three. It exists because standing the layer up today
touches three jobs and only one of them is eval work:

    A. bring the agent          -> `target:` (container or endpoint)
    B. wire tools through the mediator -> provided here, identical every time
    C. design the eval          -> policy.yaml + mocks.yaml, the only per-eval work

Collapsing A and B into provided infrastructure is what makes this adoptable, so
the setup file is the whole of A and B:

    version: 1
    target:
      kind: endpoint            # endpoint | container
      url: http://127.0.0.1:8080/v1/chat/completions
      model: telecom-support-agent
    policy: ./policy.yaml       # enforcement: pass | mock | block
    mocks: ./mocks.yaml         # fidelity: what a mocked call returns
    cassettes: ./cassettes      # optional

Nothing here edits application code or a Dockerfile.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .mediator import ActionMediator
from .mocks import MockLibrary
from .policy import MediationPolicy, _glob_match
from .tool_host import AgentHooksToolHost

SUPPORTED_VERSIONS = frozenset({1})
TARGET_KINDS = frozenset({"endpoint", "container"})


class SetupError(ValueError):
    """Raised when a setup file is malformed or points at something missing."""


@dataclass(frozen=True)
class TargetSpec:
    """Where the configured agent under test lives.

    `endpoint` — an already-running agent (local process, staged sandbox, remote
    service) reachable over HTTP. ASSERT drives it through its connector.

    `container` — an image the stock ASSERT sandbox launches and removes around
    each test case.
    """

    kind: str
    url: str | None = None
    model: str | None = None
    image: str | None = None
    command: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    port: int | None = None
    health_path: str = "/health"
    endpoint_path: str = "/chat"
    startup_timeout_s: float = 60.0
    egress_allow_hosts: tuple[str, ...] = ()
    memory: str = "1g"
    cpus: float = 1.0
    pids_limit: int = 256
    user: str = "65534:65534"
    model_proxy: dict[str, Any] | None = None

    def describe(self) -> str:
        if self.kind == "endpoint":
            return f"endpoint {self.url}"
        return f"container {self.image}"


@dataclass(frozen=True)
class MediationSetup:
    """A loaded, validated setup: everything needed to mediate a run."""

    target: TargetSpec
    policy: MediationPolicy
    mocks: MockLibrary
    source_path: Path | None = None
    policy_path: Path | None = None
    mocks_path: Path | None = None
    cassette_dir: Path | None = None

    def mediator(self) -> ActionMediator:
        return ActionMediator(self.policy, cassette_dir=self.cassette_dir, mocks=self.mocks)

    def tool_host(
        self,
        *,
        tools: Mapping[str, Any],
        agent_id: str,
        session_id: str,
        framework: str = "openclaw-mcp-sandbox",
    ) -> AgentHooksToolHost:
        """The provided job-B wiring: tools -> mediator -> evidence."""
        return AgentHooksToolHost(
            tools=tools,
            mediator=self.mediator(),
            agent_id=agent_id,
            session_id=session_id,
            framework=framework,
        )

    def summary(self) -> dict[str, Any]:
        """Human-readable summary for run provenance and startup logs."""
        return {
            "target": self.target.describe(),
            "target_kind": self.target.kind,
            "policy_rules": len(self.policy.data.get("interactions") or []),
            "policy_default": (self.policy.data.get("default") or {}).get("mode", "block"),
            "mock_rules": len(self.mocks.rules),
            "mock_tools": sorted(self.mocks.tools()),
            "cassette_dir": str(self.cassette_dir) if self.cassette_dir else None,
            "source": str(self.source_path) if self.source_path else None,
        }


def _resolve(base: Path | None, value: str) -> Path:
    p = Path(value)
    if base and not p.is_absolute():
        p = base / p
    return p


def _load_target(data: Any) -> TargetSpec:
    if not isinstance(data, Mapping):
        raise SetupError("`target:` must be a mapping with at least `kind:`")
    kind = str(data.get("kind") or "").strip().lower()
    if kind not in TARGET_KINDS:
        raise SetupError(f"`target.kind` must be one of {sorted(TARGET_KINDS)}, got {kind or '(missing)'}")

    if kind == "endpoint":
        url = str(data.get("url") or "").strip()
        if not url:
            raise SetupError("`target.kind: endpoint` requires `url:`")
        return TargetSpec(kind=kind, url=url, model=data.get("model"), port=data.get("port"))

    image = str(data.get("image") or "").strip()
    if not image:
        raise SetupError("`target.kind: container` requires `image:`")
    command = data.get("command") or []
    if command and not isinstance(command, list):
        raise SetupError("`target.command` must be a list")
    env = data.get("env") or {}
    if env and not isinstance(env, Mapping):
        raise SetupError("`target.env` must be a mapping")
    model_proxy = data.get("model_proxy")
    if model_proxy is not None and not isinstance(model_proxy, Mapping):
        raise SetupError("`target.model_proxy` must be a mapping")
    egress = data.get("egress") or {}
    if not isinstance(egress, Mapping):
        raise SetupError("`target.egress` must be a mapping")
    allow_hosts = egress.get("allow_hosts") or []
    if not isinstance(allow_hosts, list):
        raise SetupError("`target.egress.allow_hosts` must be a list")
    port = int(data.get("port") or 0)
    if not 1 <= port <= 65535:
        raise SetupError("`target.kind: container` requires `port:` between 1 and 65535")
    return TargetSpec(
        kind=kind,
        image=image,
        command=[str(c) for c in command],
        env={str(k): str(v) for k, v in env.items()},
        port=port,
        model=data.get("model"),
        health_path=str(data.get("health_path") or "/health"),
        endpoint_path=str(data.get("endpoint_path") or "/chat"),
        startup_timeout_s=float(data.get("startup_timeout_s") or 60.0),
        egress_allow_hosts=tuple(str(host) for host in allow_hosts),
        memory=str(data.get("memory") or "1g"),
        cpus=float(data.get("cpus") or 1.0),
        pids_limit=int(data.get("pids_limit") or 256),
        user=str(data.get("user") or "65534:65534"),
        model_proxy={str(key): value for key, value in model_proxy.items()} if model_proxy else None,
    )


def load_setup(path: str | Path) -> MediationSetup:
    """Load a setup file and everything it references.

    Every referenced path is resolved relative to the setup file and checked to
    exist. A typo'd mock path fails at setup, not silently mid-eval as a call
    that quietly falls through to a different response.
    """
    setup_path = Path(path)
    if not setup_path.exists():
        raise SetupError(f"setup file not found: {setup_path}")
    data = yaml.safe_load(setup_path.read_text()) or {}
    if not isinstance(data, Mapping):
        raise SetupError("setup file must be a mapping")

    version = data.get("version", 1)
    if version not in SUPPORTED_VERSIONS:
        raise SetupError(f"unsupported setup version {version!r}; supported: {sorted(SUPPORTED_VERSIONS)}")

    base = setup_path.parent
    target = _load_target(data.get("target"))

    policy_ref = data.get("policy")
    if not policy_ref:
        raise SetupError("setup file requires `policy:` (the pass/mock/block enforcement file)")
    policy_path = _resolve(base, str(policy_ref))
    if not policy_path.exists():
        raise SetupError(f"policy file not found: {policy_path}")
    policy = MediationPolicy.from_yaml(policy_path)

    cassette_dir: Path | None = None
    if data.get("cassettes"):
        cassette_dir = _resolve(base, str(data["cassettes"]))
        if not cassette_dir.exists():
            raise SetupError(f"cassette dir not found: {cassette_dir}")

    mocks_ref = data.get("mocks")
    mocks_path: Path | None = None
    if mocks_ref:
        mocks_path = _resolve(base, str(mocks_ref))
        if not mocks_path.exists():
            raise SetupError(f"mock file not found: {mocks_path}")
        mocks = MockLibrary.from_yaml(mocks_path)
        if cassette_dir and mocks.cassette_dir is None:
            mocks = MockLibrary(mocks.rules, cassette_dir=cassette_dir)
    else:
        # No mock file is legitimate: the policy's inline mocks still apply.
        mocks = MockLibrary.empty()

    return MediationSetup(
        target=target,
        policy=policy,
        mocks=mocks,
        source_path=setup_path,
        policy_path=policy_path,
        mocks_path=mocks_path,
        cassette_dir=cassette_dir or mocks.cassette_dir,
    )


def validate_setup(path: str | Path) -> dict[str, Any]:
    """Load a setup and report what it wires up, without running anything.

    This is the fast feedback loop for job C: a user edits mocks.yaml, runs
    validation, and sees which tools are covered before paying for an eval.
    """
    setup = load_setup(path)
    summary = setup.summary()

    policy_mocked = {
        str(rule.get("match"))
        for rule in (setup.policy.data.get("interactions") or [])
        if str(rule.get("mode", "")).strip().lower() in {"mock", "inline", "replay", "poison", "inject"}
    }
    mock_tools = setup.mocks.tools()
    summary["mocked_by_policy"] = sorted(policy_mocked)
    # Both sides may be globs (`send_*` in either file), so compare by pattern
    # match rather than string equality. Plain set difference reported working
    # glob rules as dead content and simultaneously claimed their tools fell
    # back to inline, which is contradictory and trains users to ignore the
    # validator.
    def _covers(patterns: set[str], name: str) -> bool:
        return any(_glob_match(pattern, name) or _glob_match(name, pattern) for pattern in patterns)

    # Mocked by policy but with no rule in the mock file: falls back to the
    # policy's inline payload. Legal, but worth surfacing.
    summary["falls_back_to_inline"] = sorted(
        tool for tool in policy_mocked if not _covers(mock_tools, tool)
    )
    # In the mock file but never mocked by policy: dead content, usually a typo.
    summary["unused_mock_rules"] = sorted(
        rule for rule in mock_tools if not _covers(policy_mocked, rule)
    )
    return summary
