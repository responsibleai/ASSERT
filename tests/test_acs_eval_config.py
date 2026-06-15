# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from assert_ai.config import load_config, load_runtime_context
from assert_ai.integrations.acs.eval_config import (
    build_eval_config,
    render_behavior_description,
    render_context,
    summarize_policy,
)
from assert_ai.stages import STAGES


_MANIFEST = """
agent_control_specification_version: "0.3"
metadata:
  name: customer_data_policy
  description: Protect customer records from disclosure.
  objective: Keep customer data inside approved workflows.
extends:
  - ./base.yaml
intervention_points:
  input:
    policy_target: input
    policy:
      id: customer_data_guard
      query: data.agent_control_specification.customer_data.input_verdict
    rules:
      - name: block_account_numbers
        decision: deny
        reason: customer_data
        message: Do not expose account numbers.
        conditions:
          - contains(input.policy_target.value, "account")
  pre_tool_call:
    policy_target: tool_call
    tool_name_from: $policy_target.name
    policy:
      id: customer_data_guard
      query: data.agent_control_specification.customer_data.pre_tool_call_verdict
tools:
  send_email:
    type: Tool
    id: send_email
  lookup_customer:
    type: Tool
    id: lookup_customer
rules:
  - name: escalate_financial_export
    point: output
    decision: escalate
    reason: financial_export
    message: Escalate bulk export responses.
    conditions:
      - contains(input.policy_target.value, "export")
x-raw-policy-dump: DO_NOT_DUMP_RAW_YAML
"""


def _write_manifest(tmp_path: Path, text: str = _MANIFEST) -> Path:
    path = tmp_path / "manifest.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_summarize_policy_returns_deterministic_manifest_summary(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path)

    summary = summarize_policy(manifest)

    assert summary.manifest_path == manifest
    assert summary.manifest_filename == "manifest.yaml"
    assert summary.policy_name == "customer_data_policy"
    assert summary.description == "Protect customer records from disclosure."
    assert summary.objective == "Keep customer data inside approved workflows."
    assert summary.extends == ("./base.yaml",)
    assert summary.intervention_points == ("input", "pre_tool_call")
    assert summary.tools == ("lookup_customer", "send_email")
    assert [(rule.name, rule.point, rule.decision) for rule in summary.rules] == [
        ("block_account_numbers", "input", "deny"),
        ("escalate_financial_export", "output", "escalate"),
    ]
    assert summary.rules[0].reason == "customer_data"
    assert summary.rules[0].message == "Do not expose account numbers."
    assert summary.rules[0].conditions == ('contains(input.policy_target.value, "account")',)


def test_summarize_policy_missing_and_invalid_manifest_have_clear_errors(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="ACS manifest not found"):
        summarize_policy(tmp_path / "missing.yaml")

    invalid = tmp_path / "invalid.yaml"
    invalid.write_text(":\n  - :\n  bad: [unterminated", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid YAML in ACS manifest"):
        summarize_policy(invalid)

    not_mapping = tmp_path / "list.yaml"
    not_mapping.write_text("- item\n", encoding="utf-8")
    with pytest.raises(ValueError, match="ACS manifest must be a YAML mapping"):
        summarize_policy(not_mapping)


def test_rendering_includes_reviewable_policy_summary_without_raw_yaml(tmp_path: Path) -> None:
    summary = summarize_policy(_write_manifest(tmp_path))

    behavior = render_behavior_description(summary)
    context = render_context(summary)
    rendered = behavior + "\n" + context

    assert "customer_data_policy" in rendered
    assert "manifest.yaml" in rendered
    assert "input, pre_tool_call" in rendered
    assert "lookup_customer, send_email" in rendered
    assert "block_account_numbers" in rendered
    assert "deny" in rendered
    assert "Do not expose account numbers." in rendered
    assert "contains(input.policy_target.value" in rendered
    assert "target callable is expected to already be guarded" in context
    assert "regression/sanity layer" in context
    assert "DO_NOT_DUMP_RAW_YAML" not in rendered
    assert "x-raw-policy-dump" not in rendered


def test_summary_redacts_secret_like_manifest_metadata(tmp_path: Path) -> None:
    manifest = _write_manifest(
        tmp_path,
        """
agent_control_specification_version: "0.3"
metadata:
  name: secret_policy
  description: token=sample-secret-value should not leak
intervention_points:
  output:
    rules:
      - name: block_secret_output
        decision: deny
        conditions:
          - api_key=sample-api-key-value
""",
    )

    summary = summarize_policy(manifest)
    rendered = render_behavior_description(summary) + render_context(summary)

    assert "[REDACTED]" in rendered
    assert "sample-secret-value" not in rendered
    assert "sample-api-key-value" not in rendered


def test_docs_frame_policy_derived_configs_as_regression_checks() -> None:
    guide = Path("docs/guides/securing-agents-with-acs.md").read_text(encoding="utf-8")

    assert "regression/sanity layer" in guide
    assert "not as the primary way to design an eval suite" in guide
    assert "Black-box behavior evals should still define the desired agent behavior and threat model first" in guide


def test_build_eval_config_loads_through_assert_runtime_context(tmp_path: Path) -> None:
    summary = summarize_policy(_write_manifest(tmp_path))

    config = build_eval_config(summary, target_callable="examples.my_agent:chat")

    assert config["suite"] == "acs-policy-validation-customer-data-policy"
    assert config["run"] == "generated-policy-eval"
    assert config["pipeline"]["inference"]["target"]["callable"] == "examples.my_agent:chat"
    assert config["pipeline"]["test_set"]["prompt"]["sample_size"] <= 12
    assert config["pipeline"]["test_set"]["scenario"]["sample_size"] <= 12
    assert set(config["pipeline"]["judge"]["dimensions"]) >= {
        "guardrail_policy_violation",
        "allowed_request_regression",
        "policy_gap",
    }

    config_path = tmp_path / "eval_config.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    raw = load_config(config_path)
    context = load_runtime_context(raw, config_path, stage_modules=STAGES)

    assert context["behavior_name"] == "acs_policy_validation"
    assert context["target"].callable == "examples.my_agent:chat"
    assert [dimension["name"] for dimension in context["evaluation"].judge.dimensions] == [
        "guardrail_policy_violation",
        "allowed_request_regression",
        "policy_gap",
    ]
