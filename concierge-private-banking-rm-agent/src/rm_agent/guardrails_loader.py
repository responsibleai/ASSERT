"""Loader and minimal validator for `policies/guardrails.yaml`.

The new YAML is keyed on the two tools (`lookup`, `draft`) plus the standard
metadata sections. The loader checks the top-level keys are present and that
the two tools have policy blocks.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


REQUIRED_TOP_LEVEL_KEYS = {
    "metadata",
    "objective",
    "actors",
    "data_classes",
    "attributes",
    "global_rules",
    "tools",
    "human_in_the_loop",
    "evaluations",
}

REQUIRED_TOOLS = {"lookup", "draft"}


@dataclass(frozen=True)
class GuardrailsSpec:
    path: Path
    raw: dict[str, Any]

    @property
    def tools(self) -> dict[str, Any]:
        return self.raw.get("tools", {}) or {}

    @property
    def global_rules(self) -> list[dict[str, Any]]:
        return list(self.raw.get("global_rules", []) or [])

    def tool_policy(self, tool_name: str) -> dict[str, Any]:
        return dict(self.tools.get(tool_name, {}) or {})

    def global_rule(self, rule_id: str) -> dict[str, Any] | None:
        for rule in self.global_rules:
            if rule.get("id") == rule_id:
                return dict(rule)
        return None

    def approved_email_domains(self) -> list[str]:
        classes = self.raw.get("data_classes", {}) or {}
        confidential = classes.get("client_confidential", {}) or {}
        domains = confidential.get("approved_domains", []) or []
        return [str(d).lower() for d in domains]

    def sanctioned_countries(self) -> list[str]:
        evals = self.raw.get("evaluations", {}) or {}
        return [str(c).lower() for c in evals.get("sanctioned_countries", []) or []]


class GuardrailsValidationError(ValueError):
    pass


def default_guardrails_path() -> Path:
    return Path(__file__).resolve().parents[2] / "policies" / "guardrails.yaml"


def load_guardrails(path: str | Path | None = None) -> GuardrailsSpec:
    guardrails_path = Path(path) if path else default_guardrails_path()
    if not guardrails_path.exists():
        raise GuardrailsValidationError(f"Guardrails file not found: {guardrails_path}")

    with guardrails_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    if not isinstance(raw, dict):
        raise GuardrailsValidationError("Guardrails YAML must parse to a mapping")

    validate_guardrails(raw)
    return GuardrailsSpec(path=guardrails_path, raw=raw)


def validate_guardrails(raw: dict[str, Any]) -> None:
    missing = sorted(REQUIRED_TOP_LEVEL_KEYS - set(raw.keys()))
    if missing:
        raise GuardrailsValidationError(
            "Missing required guardrails sections: " + ", ".join(missing)
        )
    tools = raw.get("tools") or {}
    missing_tools = sorted(REQUIRED_TOOLS - set(tools.keys()))
    if missing_tools:
        raise GuardrailsValidationError(
            "Missing required tool policies: " + ", ".join(missing_tools)
        )
