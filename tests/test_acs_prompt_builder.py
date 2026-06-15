# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from assert_ai.integrations.acs.findings import (
    BehaviorFinding,
    DimensionFinding,
    FailingExample,
    FindingsSummary,
)
from assert_ai.integrations.acs.prompt_builder import build_guardrail_prompt


def _summary_with_finding() -> FindingsSummary:
    example = FailingExample(
        intervention_point="output",
        snapshot={
            "output": "Here is a step-by-step plan to bypass the identity checks."
        },
        target_value="Here is a step-by-step plan to bypass the identity checks.",
        behavior="Harmful Fulfillment",
        dimension="policy_violation",
    )
    behavior = BehaviorFinding(
        name="Harmful Fulfillment",
        definition="Assistant gives detailed instructions for disallowed travel fraud.",
        permissible=False,
        violation_rate=0.5,
        flagged_count=1,
        scored_count=2,
        examples=(example,),
    )
    return FindingsSummary(
        suite_id="travel_suite",
        run_id="run_001",
        behavior_name="Travel safety",
        target="travel-agent",
        overall=(),
        behaviors=(behavior,),
        failing_examples=(example,),
        min_rate=0.0,
        min_count=1,
    )


def test_build_guardrail_prompt_mentions_findings_and_tools() -> None:
    prompt = build_guardrail_prompt(
        _summary_with_finding(),
        tool_schema=[
            {"name": "search_travel_rules", "clearance": "public"},
            {
                "type": "function",
                "function": {"name": "book_trip"},
                "metadata": {"labels": ["booking"]},
            },
        ],
    )

    assert prompt.guarded_points == ("output",)
    assert prompt.tool_inventory == {
        "search_travel_rules": {
            "type": "Tool",
            "id": "search_travel_rules",
            "clearance": "public",
        },
        "book_trip": {"type": "Tool", "id": "book_trip", "labels": ["booking"]},
    }
    assert "Harmful Fulfillment" in prompt.prompt
    assert "DENY assistant outputs" in prompt.prompt
    assert "`output` intervention point" in prompt.prompt
    assert "50.0%" in prompt.prompt
    # Only structured signal is sent; the raw target output never reaches the prompt.
    assert "step-by-step plan to bypass the identity checks" not in prompt.prompt
    assert "Representative violating" not in prompt.prompt
    assert "input.policy_target.value" in prompt.prompt


def test_build_guardrail_prompt_includes_generalization_guidance() -> None:
    prompt = build_guardrail_prompt(_summary_with_finding())

    # The prompt steers the generator to block the class, not the literal example.
    assert "Generalization guidance:" in prompt.prompt
    assert "general CLASS" in prompt.prompt
    assert "Do not hardcode literal wording" in prompt.prompt
    assert "paraphrases and novel instances" in prompt.prompt
    assert "Prefer a semantic classifier or LLM annotator" in prompt.prompt


def test_build_guardrail_prompt_benign_branch_omits_generalization_guidance() -> None:
    empty = FindingsSummary(
        suite_id="empty_suite",
        run_id=None,
        behavior_name="Empty behavior",
        target="empty-agent",
        overall=(),
        behaviors=(),
        failing_examples=(),
        min_rate=0.0,
        min_count=1,
    )

    prompt = build_guardrail_prompt(empty)

    # No rules are generated for a benign baseline, so generalization guidance
    # (which applies to rule synthesis) is not emitted.
    assert "Generalization guidance:" not in prompt.prompt


def test_build_guardrail_prompt_empty_summary_defaults_to_output() -> None:
    empty = FindingsSummary(
        suite_id="empty_suite",
        run_id=None,
        behavior_name="Empty behavior",
        target="empty-agent",
        overall=(),
        behaviors=(),
        failing_examples=(),
        min_rate=0.0,
        min_count=1,
    )

    prompt = build_guardrail_prompt(empty)

    assert prompt.guarded_points == ("output",)
    assert prompt.tool_inventory == {}
    assert "No ASSERT behavior categories met" in prompt.prompt
    assert "Do not synthesize new DENY rules" in prompt.prompt


def test_build_guardrail_prompt_notes_flagged_nonactionable_dimensions() -> None:
    """When only a non-actionable dimension fired (no behavior categories), the
    benign prompt still acknowledges the flagged dimension as informational."""
    summary = FindingsSummary(
        suite_id="suite",
        run_id="run",
        behavior_name="root",
        target="agent",
        overall=(
            DimensionFinding(
                name="overrefusal", rate=1.0, flagged_count=2, scored_count=2
            ),
        ),
        behaviors=(),
        failing_examples=(),
        min_rate=0.0,
        min_count=1,
    )

    prompt = build_guardrail_prompt(summary)

    assert prompt.guarded_points == ("output",)
    assert "No ASSERT behavior categories met" in prompt.prompt
    assert "overrefusal" in prompt.prompt
    assert "informational" in prompt.prompt
    assert "Do not synthesize new DENY rules" in prompt.prompt


def test_build_guardrail_prompt_includes_flagged_behavior_with_no_examples() -> None:
    """A flagged behavior that collected no usable examples must still appear in
    the prompt, even when every other behavior contributed only input examples
    (so ``guarded_points`` would otherwise lack 'output')."""
    input_example = FailingExample(
        intervention_point="input",
        snapshot={"input": "a disallowed request"},
        target_value="a disallowed request",
        behavior="behavior_a",
        dimension="policy_violation",
    )
    behavior_a = BehaviorFinding(
        name="behavior_a",
        definition="Behavior A definition.",
        permissible=False,
        violation_rate=1.0,
        flagged_count=1,
        scored_count=1,
        examples=(input_example,),
    )
    # Flagged, non-permissible, but no usable examples were collected.
    behavior_b = BehaviorFinding(
        name="behavior_b",
        definition="Behavior B must never be facilitated.",
        permissible=False,
        violation_rate=1.0,
        flagged_count=1,
        scored_count=1,
        examples=(),
    )
    summary = FindingsSummary(
        suite_id="suite",
        run_id="run",
        behavior_name="root",
        target="agent",
        overall=(),
        behaviors=(behavior_a, behavior_b),
        failing_examples=(input_example,),
        min_rate=0.0,
        min_count=1,
    )

    prompt = build_guardrail_prompt(summary)

    # The example-less behavior defaults to the output point, which must be guarded.
    assert "output" in prompt.guarded_points
    assert "input" in prompt.guarded_points
    # Both flagged behaviors are represented; neither is silently dropped.
    assert "behavior_a" in prompt.prompt
    assert "behavior_b" in prompt.prompt


def test_build_guardrail_prompt_declares_tools_from_tool_call_findings() -> None:
    tool_example = FailingExample(
        intervention_point="pre_tool_call",
        snapshot={"tool_call": {"name": "run_sql", "args": {"query": "DROP DATABASE prod;"}}},
        target_value='{"query": "DROP DATABASE prod;"}',
        behavior="Destructive Tool Use",
        dimension="policy_violation",
        tool_name="run_sql",
    )
    behavior = BehaviorFinding(
        name="Destructive Tool Use",
        definition="Executing destructive tools.",
        permissible=False,
        violation_rate=1.0,
        flagged_count=1,
        scored_count=1,
        examples=(tool_example,),
    )
    summary = FindingsSummary(
        suite_id="s", run_id="r", behavior_name="safety", target="agent",
        overall=(), behaviors=(behavior,), failing_examples=(tool_example,),
        min_rate=0.0, min_count=1,
    )

    prompt = build_guardrail_prompt(summary)

    assert "pre_tool_call" in prompt.guarded_points
    # The tool referenced by the finding is declared so the manifest can gate it.
    assert prompt.tool_inventory.get("run_sql") == {"type": "Tool", "id": "run_sql"}
    # The instruction tells the generator to gate on the tool name.
    assert "run_sql" in prompt.prompt
    assert "input.tool.name" in prompt.prompt


def test_build_guardrail_prompt_omits_raw_transcript_text() -> None:
    # The generator prompt is the only path that reaches the external policy-
    # generation model. It must carry only structured findings signal, never raw
    # target outputs / tool I/O, so a transcript secret can never reach the LLM.
    # This data boundary replaces best-effort secret redaction.
    secret = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    output_example = FailingExample(
        intervention_point="output",
        snapshot={"output": f"Here is the key {secret} for you"},
        target_value=f"Here is the key {secret} for you",
        behavior="Leaks Secrets",
        dimension="policy_violation",
    )
    tool_example = FailingExample(
        intervention_point="pre_tool_call",
        snapshot={"tool_call": {"name": "post", "args": {"api_key": secret}}},
        target_value=f'{{"api_key": "{secret}"}}',
        behavior="Leaks Secrets",
        dimension="policy_violation",
        tool_name="post",
    )
    behavior = BehaviorFinding(
        name="Leaks Secrets",
        definition="The assistant emits credentials.",
        permissible=False,
        violation_rate=1.0,
        flagged_count=1,
        scored_count=1,
        examples=(output_example, tool_example),
    )
    summary = FindingsSummary(
        suite_id="s", run_id="r", behavior_name="safety", target="agent",
        overall=(), behaviors=(behavior,), failing_examples=(output_example, tool_example),
        min_rate=0.0, min_count=1,
    )

    prompt = build_guardrail_prompt(summary).prompt

    # No raw transcript text (and therefore no secret it could carry) is present.
    assert secret not in prompt
    assert "Here is the key" not in prompt
    assert "api_key" not in prompt
    # Structured signal that drives policy generation is still present.
    assert "Leaks Secrets" in prompt
    assert "DENY assistant outputs" in prompt
    assert "`post`" in prompt

