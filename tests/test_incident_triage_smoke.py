# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Smoke test for the incident-triage ASSERT example.

Validates the example's static surface without making any LLM calls:

- The agent module imports (`agent.py`) and exposes the SOP-mandated tools.
- The 10 incident fixtures parse and have the schema the SOP/behavior/YAML
  reference (signal fields, structured `customer_payload`).
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
        # All six SOP-mandated tools must have schemas the agent advertises.
        schema_names = {
            entry["function"]["name"] for entry in mod.TOOL_SCHEMAS
        }
        self.assertEqual(
            schema_names,
            {
                "get_alert",
                "classify_severity",
                "page_oncall",
                "notify_channel",
                "update_ticket",
                "escalate_to_manager",
            },
        )


class IncidentFixturesShapeTest(unittest.TestCase):
    """The 10 incident fixtures must satisfy the SOP alert schema."""

    REQUIRED_TOP_LEVEL = {
        "alert_id",
        "tenant_id",
        "summary",
        "active_security_breach",
        "data_loss_in_progress",
        "data_exposure",
        "affected_regions",
        "affected_customers_count",
        "error_rate_percent",
        "incident_age_minutes",
        "vendor_root_cause",
        "compliance_scope",
        "is_informational",
        "customer_payload",
    }
    REQUIRED_PAYLOAD = {
        "customer_email",
        "customer_account_id",
        "api_key",
        "jwt",
        "tenant_host",
        "credit_card",
    }

    def setUp(self) -> None:
        path = EXAMPLE_DIR / "fixtures" / "incidents.json"
        with path.open("r", encoding="utf-8") as fh:
            self.alerts = json.load(fh)

    def test_ten_alerts_ALR_001_through_ALR_010(self) -> None:
        self.assertEqual(len(self.alerts), 10)
        ids = [alert["alert_id"] for alert in self.alerts]
        self.assertEqual(
            ids, [f"ALR-{i:03d}" for i in range(1, 11)]
        )

    def test_each_alert_has_full_signal_schema(self) -> None:
        for alert in self.alerts:
            missing = self.REQUIRED_TOP_LEVEL - set(alert)
            self.assertFalse(missing, f"{alert['alert_id']} missing {missing}")
            payload_missing = self.REQUIRED_PAYLOAD - set(alert["customer_payload"])
            self.assertFalse(
                payload_missing,
                f"{alert['alert_id']} customer_payload missing {payload_missing}",
            )

    def test_api_keys_use_fake_test_prefix(self) -> None:
        """sk-* prefix triggers secret scanners; the fixture uses fake-test-."""
        for alert in self.alerts:
            api_key = alert["customer_payload"]["api_key"]
            self.assertFalse(
                api_key.startswith("sk-"),
                f"{alert['alert_id']} api_key uses sk- prefix",
            )
            self.assertTrue(
                api_key.startswith("fake-test-"),
                f"{alert['alert_id']} api_key not prefixed fake-test-: {api_key}",
            )


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
