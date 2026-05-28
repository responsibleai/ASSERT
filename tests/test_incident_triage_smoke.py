# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Smoke test for the joint AgentShield + p2m incident-triage demo.

Validates the demo's static surface without making any LLM calls:

- Both agent modules import (`agent.py`, `agent_guarded.py`).
- The AgentShield runtime constructs from the YAML (skipped if the
  optional `agent_shield` package is not installed).
- The `.guardrails.yaml` parses and has the expected top-level shape.
- The 10 incident fixtures parse and have the schema the SOP/behavior/YAML
  reference (signal fields, structured `customer_payload`).
- The two eval configs parse and point at the matching callable targets.

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


class IncidentTriageGuardedImportTest(unittest.TestCase):
    """The guarded agent + AgentShield runtime construction.

    Skipped when the optional `agent_shield` extra is not installed (the
    case in the default p2m dev environment).
    """

    def test_guarded_agent_runtime_builds_from_yaml(self) -> None:
        pytest.importorskip("agent_shield")
        mod = importlib.import_module(
            "examples.incident_triage_agent.agent_guarded"
        )
        self.assertTrue(callable(getattr(mod, "chat", None)))
        # _RUNTIME is built once at import time; surface it for the test.
        self.assertIsNotNone(getattr(mod, "_RUNTIME", None))


class GuardrailsYamlShapeTest(unittest.TestCase):
    """The .guardrails.yaml is the source of truth for runtime behaviour.

    Keep its top-level shape stable so future spec changes are deliberate.
    """

    def setUp(self) -> None:
        self.yaml_path = DEMO_DIR / "incident-triage.guardrails.yaml"
        self.assertTrue(self.yaml_path.exists())
        with self.yaml_path.open("r", encoding="utf-8") as fh:
            self.payload = yaml.safe_load(fh)

    def test_top_level_keys_match_agentshield_v1_spec(self) -> None:
        # AgentShield v1 spec: every YAML must have these top-level keys.
        for key in (
            "metadata",
            "objective",
            "variables",
            "predicates",
            "state_validation",
            "tool_execution_validation",
            "resources",
        ):
            self.assertIn(key, self.payload, f"missing top-level key: {key}")

    def test_state_and_tool_exec_have_named_guards(self) -> None:
        state = self.payload["state_validation"]["guard_policies"]
        tool_exec = self.payload["tool_execution_validation"]["guard_policies"]
        self.assertGreater(len(state), 0)
        self.assertGreater(len(tool_exec), 0)
        # Every guard must have a `name:` (the README rule-name table
        # references these literal names).
        for guard in [*state, *tool_exec]:
            self.assertIn("name", guard)
            self.assertTrue(guard["name"])

    def test_critical_guard_names_present(self) -> None:
        """README and case study reference these guard names literally."""
        all_names = {
            g["name"]
            for g in (
                *self.payload["state_validation"]["guard_policies"],
                *self.payload["tool_execution_validation"]["guard_policies"],
            )
        }
        for required in (
            "alert_must_be_loaded_gate",
            "classify_before_action_gate",
            "pager_severity_gate",
            "channel_severity_match_gate",
            "pii_leak_gate",
            "alert_id_consistency_gate",
            "escalation_obligation_gate",
            "escalation_team_match_gate",
        ):
            self.assertIn(required, all_names, f"missing guard: {required}")


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
    """Both eval configs must point at the matching callable target."""

    def setUp(self) -> None:
        self.baseline_path = DEMO_DIR / "eval_config_baseline.yaml"
        self.guarded_path = DEMO_DIR / "eval_config_guarded.yaml"
        with self.baseline_path.open("r", encoding="utf-8") as fh:
            self.baseline = yaml.safe_load(fh)
        with self.guarded_path.open("r", encoding="utf-8") as fh:
            self.guarded = yaml.safe_load(fh)

    def test_targets_point_at_matching_modules(self) -> None:
        baseline_target = (
            self.baseline["pipeline"]["inference"]["target"]["callable"]
        )
        guarded_target = (
            self.guarded["pipeline"]["inference"]["target"]["callable"]
        )
        self.assertEqual(
            baseline_target, "examples.incident_triage_agent.agent:chat"
        )
        self.assertEqual(
            guarded_target,
            "examples.incident_triage_agent.agent_guarded:chat",
        )

    def test_configs_share_suite_so_test_set_cache_is_reused(self) -> None:
        # The whole point of the BEFORE/AFTER pair is that they share
        # systematization.json / stratification.json / test_set.jsonl. If
        # suites diverge, the comparison is no longer apples-to-apples.
        self.assertEqual(self.baseline["suite"], self.guarded["suite"])

    def test_max_turns_matches_between_configs(self) -> None:
        # max_turns asymmetry between BEFORE and AFTER would bias the
        # overrefusal-trade-off measurement (the headline finding).
        baseline_turns = self.baseline["pipeline"]["inference"]["max_turns"]
        guarded_turns = self.guarded["pipeline"]["inference"]["max_turns"]
        self.assertEqual(baseline_turns, guarded_turns)


if __name__ == "__main__":
    unittest.main()
