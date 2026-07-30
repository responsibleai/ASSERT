# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Policy loading and rule matching for action mediation."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


def _glob_match(pattern: str, name: str) -> bool:
    if pattern in (name, "*"):
        return True
    if pattern.endswith("*") and name.startswith(pattern[:-1]):
        return True
    if pattern.startswith("*") and name.endswith(pattern[1:]):
        return True
    return False


@dataclass(frozen=True)
class MediationPolicy:
    data: dict[str, Any]

    @classmethod
    def from_yaml(cls, path: str | Path) -> "MediationPolicy":
        return cls(yaml.safe_load(Path(path).read_text()) or {})

    @classmethod
    def from_json(cls, path: str | Path) -> "MediationPolicy":
        return cls(json.loads(Path(path).read_text()))

    def decide(self, tool_name: str) -> dict[str, Any]:
        for rule in self.data.get("interactions") or []:
            if _glob_match(str(rule.get("match", "")), tool_name):
                return dict(rule)
        default = self.data.get("default") or {"mode": "block", "note": "deny unknown tool"}
        return {"match": "<default>", "mode": default.get("mode", "block"), "note": default.get("note", "")}
