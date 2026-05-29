# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Smoke test for the incident-triage ASSERT example.

Validates the example's static surface without making any LLM calls:

- The agent module imports (`agent.py`) and exposes the local fixture tools.
- The incident fixtures parse from `fixtures.json` and match the behavior/config.
- The eval config parses and points at the matching callable target.

Runs in <2 seconds, no network, no API keys. Gated by
`.github/workflows/regression.yml` so doc/spec changes that drift this
example will fail CI before the doc PR can land.
"""

from __future__ import annotations

import importlib
import json
import sys
import unittest
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_DIR = REPO_ROOT / "examples" / "incident_triage_agent"


@pytest.fixture(autouse=True, scope="module")
def _ensure_repo_on_syspath():
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    yield


class IncidentTriageAgentImportTest(unittest.TestCase):
    """The agent module is the contract for every other artifact."""

    def test_agent_imports_and_exposes_chat(self) -> None:
        mod = importlib.import_module("examples.incident_triage_agent.agent")
        self.assertTrue(callable(getattr(mod, "chat", None)))
        self.assertTrue(getattr(mod, "SYSTEM_PROMPT", "").strip())
        self.assertTrue(getattr(mod, "AGENT_MODEL", "").startswith("azure/"))
        schema_names = {
            entry["function"]["name"] for entry in mod.TOOL_SCHEMAS
        }
        self.assertEqual(
            schema_names,
            {
                "get_alert",
                "post_to_channel",
                "escalate",
                "acknowledge_oncall_page",
            },
        )


class IncidentFixturesShapeTest(unittest.TestCase):
    """The local incident fixtures must satisfy the tool schema."""

    REQUIRED_TOP_LEVEL = {
        "alert_id",
        "severity",
        "summary",
        "customer_team",
        "needs_escalation_to",
    }

    def setUp(self) -> None:
        path = EXAMPLE_DIR / "fixtures.json"
        with path.open("r", encoding="utf-8") as fh:
            self.alerts = json.load(fh)

    def test_three_alerts_ALR_001_through_ALR_003(self) -> None:
        self.assertEqual(len(self.alerts), 3)
        ids = [alert["alert_id"] for alert in self.alerts]
        self.assertEqual(
            ids, [f"ALR-{i:03d}" for i in range(1, 4)]
        )

    def test_each_alert_has_fixture_tool_schema(self) -> None:
        for alert in self.alerts:
            missing = self.REQUIRED_TOP_LEVEL - set(alert)
            self.assertFalse(missing, f"{alert['alert_id']} missing {missing}")
            self.assertTrue(alert["severity"])
            self.assertTrue(alert["summary"])
            self.assertTrue(alert["customer_team"])
            escalation = alert["needs_escalation_to"]
            self.assertTrue(escalation is None or isinstance(escalation, str))


class EvalConfigShapeTest(unittest.TestCase):
    """The eval config must point at the matching callable target."""

    def setUp(self) -> None:
        self.config_path = EXAMPLE_DIR / "eval_config.yaml"
        with self.config_path.open("r", encoding="utf-8") as fh:
            self.config = yaml.safe_load(fh)

    def test_target_points_at_agent_module(self) -> None:
        target = self.config["pipeline"]["inference"]["target"]["callable"]
        self.assertEqual(target, "examples.incident_triage_agent.agent:chat")

    def test_suite_id_matches_example_name(self) -> None:
        # README and CI references rely on this suite ID being stable.
        self.assertEqual(self.config["suite"], "incident-triage-agent-v1")


if __name__ == "__main__":
    unittest.main()
