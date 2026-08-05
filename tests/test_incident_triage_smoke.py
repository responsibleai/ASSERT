# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Smoke test for the baseline ASSERT incident-triage example.

Validates the demo's static surface without making any LLM calls:

- The baseline agent imports (`agent.py`) and advertises the six SOP tools.
- The 10 incident fixtures parse and have the schema the SOP/behavior/YAML
  reference (signal fields, structured `customer_payload`).
- The baseline eval config and every one-behavior-per-YAML config parse and
  point at the baseline callable target (no guarded/GEPA surface remains).

Runs in <2 seconds, no network, no API keys. Gated by
`.github/workflows/regression.yml` so doc/spec changes that drift this
demo will fail CI before the doc PR can land.
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
DEMO_DIR = REPO_ROOT / "examples" / "incident_triage_agent"


@pytest.fixture(autouse=True, scope="module")
def _ensure_repo_on_syspath():
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    yield


class IncidentTriageBaselineImportTest(unittest.TestCase):
    """The baseline agent module is the contract for every other artifact."""

    def test_baseline_agent_imports_and_exposes_chat(self) -> None:
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
        path = DEMO_DIR / "fixtures" / "incidents.json"
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
    """The baseline config and every one-behavior-per-YAML config must point
    at the baseline callable target — there is no guarded target anymore."""

    BASELINE_TARGET = "examples.incident_triage_agent.agent:chat"

    def setUp(self) -> None:
        self.baseline_path = DEMO_DIR / "eval_config_baseline.yaml"
        with self.baseline_path.open("r", encoding="utf-8") as fh:
            self.baseline = yaml.safe_load(fh)
        self.behavior_paths = sorted((DEMO_DIR / "behaviors").glob("*.yaml"))
        self.behaviors = {}
        for path in self.behavior_paths:
            with path.open("r", encoding="utf-8") as fh:
                self.behaviors[path.name] = yaml.safe_load(fh)

    def test_nine_one_behavior_configs_present(self) -> None:
        self.assertEqual(len(self.behavior_paths), 9)

    def test_every_config_targets_the_baseline_callable(self) -> None:
        baseline_target = (
            self.baseline["pipeline"]["inference"]["target"]["callable"]
        )
        self.assertEqual(baseline_target, self.BASELINE_TARGET)
        for name, cfg in self.behaviors.items():
            target = cfg["pipeline"]["inference"]["target"]["callable"]
            self.assertEqual(
                target, self.BASELINE_TARGET, f"{name} targets {target}"
            )

    def test_no_config_references_a_guarded_target(self) -> None:
        # The guarded/GEPA surface is gone; nothing may point at it.
        configs = {"eval_config_baseline.yaml": self.baseline, **self.behaviors}
        for name, cfg in configs.items():
            target = cfg["pipeline"]["inference"]["target"]["callable"]
            self.assertNotIn(
                "guarded", target, f"{name} still targets a guarded callable"
            )

    def test_each_behavior_is_its_own_suite(self) -> None:
        # One behavior per YAML: each config carries a distinct suite so its
        # adversarial test set stays concentrated on a single failure mode.
        suites = [cfg["suite"] for cfg in self.behaviors.values()]
        self.assertEqual(
            len(suites), len(set(suites)), "behavior suites must be distinct"
        )
        self.assertNotIn(self.baseline["suite"], suites)


if __name__ == "__main__":
    unittest.main()
