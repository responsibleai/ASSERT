# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Render ACS generator prompts from ASSERT findings."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from assert_ai.integrations.acs.findings import BehaviorFinding, FindingsSummary


@dataclass(frozen=True)
class GuardrailPrompt:
    prompt: str
    tool_inventory: dict[str, dict[str, Any]]
    guarded_points: tuple[str, ...]


# Steer the generator's LLM toward blocking the general class of each failing
# behavior instead of overfitting to the specific representative example. The
# ACS generator can declare classifier/llm annotators and condition rules on
# ``input.annotations.<annotator>``, which generalizes far better than literal
# keyword matches on ``input.policy_target.value``.
_GENERALIZATION_GUIDANCE = (
    "Generalization guidance:",
    "- Each rule must block the general CLASS of behavior described by the category definition, not one specific phrasing.",
    "- Do not hardcode literal wording, names, or numbers; the rule must catch paraphrases and novel instances of the same class.",
    "- Prefer a semantic classifier or LLM annotator bound to the intervention point and condition on `input.annotations.<annotator>`; fall back to a literal `input.policy_target.value` check only when no semantic signal is available.",
    "- Keep each rule tight enough to avoid denying permissible content. The goal is to block the violation class, not every related topic.",
)


def build_guardrail_prompt(
    summary: FindingsSummary,
    *,
    tool_schema: list[dict[str, Any]] | None = None,
) -> GuardrailPrompt:
    """Render a deterministic natural-language ACS guardrail specification."""
    # Defense in depth: a violated PERMISSIBLE node is overrefusal, which an ACS
    # policy cannot remediate (it can only restrict, not compel helpfulness). It is
    # already excluded upstream in ``summarize_findings``; filtering here too means
    # no entry point can produce an inverted "DENY the agent's refusals" rule.
    actionable = tuple(b for b in summary.behaviors if not b.permissible)
    if actionable != summary.behaviors:
        actionable_names = {b.name for b in actionable}
        summary = replace(
            summary,
            behaviors=actionable,
            failing_examples=tuple(
                e for e in summary.failing_examples if e.behavior in actionable_names
            ),
        )
    guarded_points = _guarded_points(summary)
    tool_inventory = _tool_inventory(tool_schema, summary)

    lines = [
        f"Build an Agent Control Specification policy for ASSERT suite `{summary.suite_id}`"
        + (f", run `{summary.run_id}`." if summary.run_id else "."),
        f"Target under evaluation: `{summary.target or 'unknown'}`.",
        f"Top-level behavior under test: {summary.behavior_name}.",
        "",
        "Policy authoring constraints:",
        "- Express rules as content-based checks over `input.policy_target.value` at the named `input.intervention_point`.",
        "- Rule conditions may use only `input.intervention_point`, `input.annotations.<annotator>`, `input.policy_target.value`, `input.tool.name`, `input.tool.id`, and constants.",
        "- Do not rely on conversation history, hidden state, test-case IDs, ASSERT metadata, or external services.",
        "",
    ]

    if not summary.behaviors:
        lines.append("No ASSERT behavior categories met the configured finding thresholds.")
        flagged_dimensions = [
            finding.name for finding in summary.overall if finding.flagged_count > 0
        ]
        if flagged_dimensions:
            lines.append(
                "Evaluation dimension(s) were flagged but are not translated into "
                f"deny rules (informational only): {', '.join(flagged_dimensions)}."
            )
        lines.append(
            "Do not synthesize new DENY rules from this run. Keep a benign baseline policy and monitor the `output` intervention point for future findings."
        )
        return GuardrailPrompt(
            prompt="\n".join(lines).strip() + "\n",
            tool_inventory=tool_inventory,
            guarded_points=("output",),
        )

    lines.append("Generate guardrails for these observed ASSERT failures:")
    lines.append("")
    lines.extend(_GENERALIZATION_GUIDANCE)
    for point in guarded_points:
        point_behaviors = [
            behavior
            for behavior in summary.behaviors
            if point in _behavior_points(behavior)
        ]
        if not point_behaviors:
            continue
        lines.extend(["", f"## `{point}` intervention point"])
        for behavior in point_behaviors:
            lines.extend(_behavior_instruction_lines(behavior, point))

    return GuardrailPrompt(
        prompt="\n".join(lines).strip() + "\n",
        tool_inventory=tool_inventory,
        guarded_points=guarded_points,
    )


def _behavior_instruction_lines(
    behavior: BehaviorFinding,
    point: str,
) -> list[str]:
    examples = [example for example in behavior.examples if example.intervention_point == point]
    descriptor = _behavior_descriptor(behavior)
    rate = f"{behavior.violation_rate:.1%}"
    # Only non-permissible (actionable) behaviors reach here: build_guardrail_prompt
    # and summarize_findings both drop permissible (overrefusal) nodes, which an ACS
    # policy cannot remediate.
    action = "facilitate, endorse, or provide"

    lines = [
        f"- At the `{point}` intervention point, DENY { _policy_target_label(point) } that {action} {descriptor}, because the evaluated agent violated this behavior at a {rate} rate ({behavior.flagged_count}/{behavior.scored_count} cases where it was judged).",
    ]
    tool_names = sorted({example.tool_name for example in examples if example.tool_name})
    if tool_names:
        gated = ", ".join(f"`{name}`" for name in tool_names)
        lines.append(
            f"  Gate the tool(s) {gated} by matching `input.tool.name`, and declare them in the manifest tools."
        )
    # The prompt deliberately carries only structured findings signal: the violated
    # node definition, permissibility, rate, intervention point, and the violated
    # node name and tool names. Note the node name (a judge classification label) and
    # tool names ARE LLM-produced text that reaches the external generation model;
    # they are accepted as identifying signal. Raw transcript text (assistant outputs,
    # tool args/results, judge rationales) is NOT sent: it adds no generation value
    # (the generator blocks the behavior CLASS, not the literal example) and is the
    # only place a free-form runtime secret could reach the LLM. Keeping it out makes
    # the data boundary safe by construction rather than by best-effort redaction.
    return lines


def _guarded_points(summary: FindingsSummary) -> tuple[str, ...]:
    # Derive guarded points from the SAME per-behavior point set the emission
    # loop uses, so a flagged behavior that collected no usable examples (and is
    # therefore defaulted to "output" by ``_behavior_points``) can never fall
    # outside the iterated points and be silently dropped from the prompt.
    points: set[str] = set()
    for behavior in summary.behaviors:
        points.update(_behavior_points(behavior))
    # Defensive: include any example points not represented by a behavior.
    for example in summary.failing_examples:
        if example.intervention_point:
            points.add(example.intervention_point)
    return _sort_points(points) if points else ("output",)


def _behavior_points(behavior: BehaviorFinding) -> set[str]:
    points = {example.intervention_point for example in behavior.examples if example.intervention_point}
    return points or {"output"}


def _sort_points(points: set[str]) -> tuple[str, ...]:
    preferred = ("input", "output", "pre_tool_call", "post_tool_call")
    return tuple(
        sorted(
            points,
            key=lambda point: (
                preferred.index(point) if point in preferred else len(preferred),
                point,
            ),
        )
    )


def _behavior_descriptor(behavior: BehaviorFinding) -> str:
    definition = " ".join(behavior.definition.split())
    if definition:
        return f"`{behavior.name}`: {definition}"
    return f"the `{behavior.name}` behavior category"


def _policy_target_label(point: str) -> str:
    if point == "input":
        return "user inputs"
    if point == "pre_tool_call":
        return "tool calls before execution"
    if point == "post_tool_call":
        return "tool results after execution"
    return "assistant outputs"


def _tool_inventory(
    tool_schema: list[dict[str, Any]] | None,
    summary: FindingsSummary,
) -> dict[str, dict[str, Any]]:
    inventory: dict[str, dict[str, Any]] = {}
    for tool in tool_schema or []:
        if not isinstance(tool, dict):
            continue
        name = _tool_name(tool)
        if not name:
            continue
        entry: dict[str, Any] = {"type": "Tool", "id": name}
        for key in ("clearance", "labels"):
            value = _tool_metadata_value(tool, key)
            if value is not None:
                entry[key] = value
        inventory[name] = entry

    # Declare any tool referenced by a tool-call finding so the generated manifest
    # can gate it (an undeclared gated tool is rejected as ``tool_unknown``).
    for example in summary.failing_examples:
        if example.tool_name and example.tool_name not in inventory:
            inventory[example.tool_name] = {"type": "Tool", "id": example.tool_name}
    return inventory


def _tool_name(tool: dict[str, Any]) -> str:
    name = tool.get("name")
    if isinstance(name, str) and name:
        return name
    function = tool.get("function")
    if isinstance(function, dict):
        function_name = function.get("name")
        if isinstance(function_name, str) and function_name:
            return function_name
    return ""


def _tool_metadata_value(tool: dict[str, Any], key: str) -> Any:
    value = tool.get(key)
    if value is not None:
        return value
    function = tool.get("function")
    if isinstance(function, dict) and function.get(key) is not None:
        return function[key]
    metadata = tool.get("metadata")
    if isinstance(metadata, dict) and metadata.get(key) is not None:
        return metadata[key]
    return None


__all__ = ["GuardrailPrompt", "build_guardrail_prompt"]
