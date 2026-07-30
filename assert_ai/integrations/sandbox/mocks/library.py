# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""The separate, modular mock file.

The setup flow points ASSERT at a container or endpoint, declares the tool calls
and scenarios to mock in a **separate modular YAML**, starts the sandbox, and
returns eval results.

This module owns that file. It is deliberately *not* the enforcement policy:

    policy.yaml   -> WHETHER a call is passed, mocked, or blocked   (safety)
    mocks.yaml    -> WHAT a mocked call returns                     (fidelity)

Keeping them apart is what makes the base policy reusable per agent while mock
content varies per scenario, and it means a user can add a use-case mock without
touching the safety posture, application code, or the Dockerfile.

File shape:

    version: 1
    cassette_dir: ./cassettes        # optional, for replay
    mocks:
      - tool: apply_bill_credit      # tool name or glob
        response:                    # inline is the default backend
          status: credit_applied

      - tool: send_message           # per-use-case: match on ARGS
        when:
          recipient: {not: "555-123-2002"}
        response:
          status: sent
        note: unverified destination

      - tool: transfer_funds
        backend: scenario
        scenario: account_transfer
        responses:
          - response: {status: ok}
            sets_state: transferred

Rules are matched most-specific-first (most declared `when:` matchers wins),
then by file order, so a narrow use-case mock and a broad tool-wide fallback can
live in the same file without ordering games.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ..policy import _glob_match
from .backends import MockBackend, MockCall, Resolution, ScenarioBackend, default_backends
from .matching import match_args, specificity

SUPPORTED_VERSIONS = frozenset({1})


class MockConfigError(ValueError):
    """Raised when a mock file is malformed. Fail loudly, never guess."""


@dataclass(frozen=True)
class MockRule:
    tool: str
    when: dict[str, Any]
    backend: str
    raw: dict[str, Any]
    order: int
    note: str = ""

    @property
    def specificity(self) -> int:
        return specificity(self.when)


def _require_mapping(value: Any, what: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise MockConfigError(f"{what} must be a mapping, got {type(value).__name__}")
    return dict(value)


class MockLibrary:
    """Resolves a mocked tool call to concrete response content.

    The mediator decides *that* a call is mocked. This decides *what it returns*.
    """

    def __init__(
        self,
        rules: list[MockRule],
        *,
        backends: Mapping[str, MockBackend] | None = None,
        cassette_dir: str | Path | None = None,
    ) -> None:
        self.rules = sorted(rules, key=lambda r: (-r.specificity, r.order))
        self.backends: dict[str, MockBackend] = dict(backends or default_backends(cassette_dir))
        self.cassette_dir = Path(cassette_dir) if cassette_dir else None
        self._validate_backends()

    def _validate_backends(self) -> None:
        for rule in self.rules:
            if rule.backend not in self.backends:
                raise MockConfigError(
                    f"mock for '{rule.tool}' asks for unknown backend '{rule.backend}'; "
                    f"available: {sorted(self.backends)}"
                )

    # ---- loading ---------------------------------------------------------

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any] | None,
        *,
        base_dir: str | Path | None = None,
        backends: Mapping[str, MockBackend] | None = None,
    ) -> "MockLibrary":
        data = _require_mapping(data, "mock file")
        version = data.get("version", 1)
        if version not in SUPPORTED_VERSIONS:
            raise MockConfigError(
                f"unsupported mock file version {version!r}; supported: {sorted(SUPPORTED_VERSIONS)}"
            )

        cassette_dir = data.get("cassette_dir")
        resolved_cassettes: Path | None = None
        if cassette_dir:
            resolved_cassettes = Path(cassette_dir)
            if base_dir and not resolved_cassettes.is_absolute():
                resolved_cassettes = Path(base_dir) / resolved_cassettes

        entries = data.get("mocks") or []
        if not isinstance(entries, list):
            raise MockConfigError("`mocks:` must be a list")

        rules: list[MockRule] = []
        for index, entry in enumerate(entries):
            entry = _require_mapping(entry, f"mocks[{index}]")
            tool = str(entry.get("tool") or "").strip()
            if not tool:
                raise MockConfigError(f"mocks[{index}] is missing `tool:`")
            when = _require_mapping(entry.get("when"), f"mocks[{index}].when")
            backend = str(entry.get("backend") or "").strip().lower()
            if not backend:
                backend = _infer_backend(entry)
            rules.append(
                MockRule(
                    tool=tool,
                    when=when,
                    backend=backend,
                    raw=entry,
                    order=index,
                    note=str(entry.get("note") or ""),
                )
            )
        return cls(rules, backends=backends, cassette_dir=resolved_cassettes)

    @classmethod
    def from_yaml(
        cls,
        path: str | Path,
        *,
        backends: Mapping[str, MockBackend] | None = None,
    ) -> "MockLibrary":
        p = Path(path)
        return cls.from_dict(yaml.safe_load(p.read_text()) or {}, base_dir=p.parent, backends=backends)

    @classmethod
    def empty(cls) -> "MockLibrary":
        return cls([])

    # ---- resolution ------------------------------------------------------

    def find(self, call: MockCall) -> MockRule | None:
        """The most specific rule whose tool, args, and scenario state all match."""
        scenario_backend = self.backends.get("scenario")
        for rule in self.rules:
            if not _glob_match(rule.tool, call.tool):
                continue
            if not match_args(rule.when, call.args):
                continue
            if rule.backend == "scenario" and isinstance(scenario_backend, ScenarioBackend):
                if not scenario_backend.matches_state(rule.raw):
                    continue
            return rule
        return None

    def resolve(self, call: MockCall) -> Resolution | None:
        """Produce mock content for this call, or None if no rule applies.

        Returning None is meaningful: it means the mock file has nothing to say
        about this call, and the caller should fall back to whatever the policy
        declares. It is never a silent empty response.
        """
        rule = self.find(call)
        if rule is None:
            return None
        backend = self.backends[rule.backend]
        resolution = backend.resolve(rule.raw, call)
        detail = dict(resolution.detail)
        detail.update({"mock_rule": rule.tool, "backend": rule.backend})
        if rule.when:
            detail["matched_args"] = sorted(rule.when)
        if rule.note:
            detail["note"] = rule.note
        return Resolution(
            value=resolution.value,
            mock_source=resolution.mock_source,
            is_error=resolution.is_error,
            state_note=resolution.state_note,
            detail=detail,
        )

    def reset(self) -> None:
        """Reset per-run state (scenario cursors) between cases."""
        for backend in self.backends.values():
            reset = getattr(backend, "reset", None)
            if callable(reset):
                reset()

    def tools(self) -> set[str]:
        return {rule.tool for rule in self.rules}


def _infer_backend(entry: Mapping[str, Any]) -> str:
    """Pick a backend from the rule's shape when it is not declared.

    Same principle the mediator already uses for `mock_source`: provenance is a
    fact about the rule's shape, not a label the author has to remember to keep
    in sync.
    """
    if "scenario" in entry or "responses" in entry:
        return "scenario"
    if "cassette" in entry or "cassette_file" in entry or "overrides" in entry:
        return "replay"
    return "inline"
