# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Summarize ASSERT judge findings for ACS policy generation."""

from __future__ import annotations

import json
import logging
from dataclasses import KW_ONLY, dataclass
from pathlib import Path
from typing import Any

from assert_ai.core.io import (
    INFERENCE_SET_FILE,
    SCORES_FILE,
    definitions_by_behavior,
    load_jsonl,
    load_policy,
    permissible_by_behavior,
    row_behavior,
)
from assert_ai.core.judge import get_verdict_dimension, infer_judge_status, is_valid_event_flag
from assert_ai.core.transcript import _collect_messages_for_view
from assert_ai.results import compute_dimension_summary, detect_dimensions

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DimensionFinding:
    name: str
    rate: float
    flagged_count: int
    scored_count: int


@dataclass(frozen=True)
class FailingExample:
    intervention_point: str
    snapshot: dict[str, Any]
    target_value: str
    behavior: str
    dimension: str
    # Keyword-only so a positional caller can never bind transcript text (e.g. a
    # former judge rationale) onto tool_name, which is emitted into the generator
    # prompt and tool inventory. Fails closed against field-order drift.
    _: KW_ONLY
    tool_name: str | None = None


@dataclass(frozen=True)
class BehaviorFinding:
    name: str
    definition: str
    permissible: bool
    violation_rate: float
    flagged_count: int
    scored_count: int
    examples: tuple[FailingExample, ...]


@dataclass(frozen=True)
class FindingsSummary:
    suite_id: str
    run_id: str | None
    behavior_name: str
    target: str
    overall: tuple[DimensionFinding, ...]
    behaviors: tuple[BehaviorFinding, ...]
    failing_examples: tuple[FailingExample, ...]
    min_rate: float
    min_count: int

    @property
    def has_findings(self) -> bool:
        """True when there are actionable behavior findings to guard against.

        Only ``policy_violation`` behavior categories drive generated DENY rules,
        so a flagged non-actionable dimension on its own (e.g. ``overrefusal`` or
        a custom dimension, retained in ``overall`` for reporting) does not count
        here, because the generated policy would be a benign baseline.
        """
        return bool(self.behaviors)


def summarize_findings(
    score_rows: list[dict[str, Any]],
    inference_rows: list[dict[str, Any]],
    taxonomy: dict[str, Any] | None,
    *,
    suite_id: str,
    run_id: str | None,
    min_rate: float = 0.0,
    min_count: int = 1,
    max_examples_per_behavior: int = 3,
) -> FindingsSummary:
    """Build an ACS-oriented summary from in-memory ASSERT score and inference rows."""
    if not 0.0 <= min_rate <= 1.0:
        raise ValueError("min_rate must be between 0.0 and 1.0")
    if min_count < 0:
        raise ValueError("min_count must be non-negative")
    if max_examples_per_behavior < 0:
        raise ValueError("max_examples_per_behavior must be non-negative")

    scored_rows = [row for row in score_rows if infer_judge_status(row) == "ok"]
    inference_by_id = _index_inference_rows(inference_rows)

    overall = tuple(
        finding
        for finding in (
            _dimension_finding(scored_rows, dimension)
            for dimension in detect_dimensions(scored_rows)
        )
        if finding is not None
        and finding.rate >= min_rate
        and finding.scored_count >= min_count
    )

    definitions = definitions_by_behavior(taxonomy)
    permissible = permissible_by_behavior(taxonomy)
    node_metrics = _node_violation_metrics(scored_rows)

    behavior_findings: list[BehaviorFinding] = []
    flat_examples: list[FailingExample] = []
    seen_examples: set[tuple[str, str | None, str, str]] = set()

    for node_name in sorted(node_metrics):
        metric = node_metrics[node_name]
        rate = metric["rate"]
        flagged_count = metric["flagged_count"]
        scored_count = metric["scored_count"]
        if flagged_count <= 0 or rate < min_rate or scored_count < min_count:
            continue

        resolved = node_name in permissible
        is_permissible = permissible.get(node_name, False)
        if resolved and is_permissible:
            # A violated PERMISSIBLE node is overrefusal (the agent wrongly refused
            # a permissible request). ACS guardrails can only restrict actions, never
            # compel helpfulness, so this is not an actionable DENY target; a rule
            # here would deny the agent's refusals and suppress legitimate ones. The
            # overrefusal dimension remains in ``overall`` for reporting.
            continue
        if not resolved:
            # No taxonomy entry for this violated node: we cannot describe it or
            # determine its permissibility. Surface the gap instead of silently
            # emitting an opaque, possibly mis-directed rule (it is assumed
            # non-permissible so the guardrail fails safe toward restriction).
            logger.warning(
                "ACS findings: violated node %r is not in the taxonomy; the generated "
                "rule will be opaque and assumed non-permissible. Provide taxonomy.json "
                "for accurate guardrail generation.",
                node_name,
            )

        examples = tuple(
            _collect_node_examples(
                node_name,
                scored_rows,
                inference_by_id,
                max_examples=max_examples_per_behavior,
            )
        )
        behavior_findings.append(
            BehaviorFinding(
                name=node_name,
                definition=definitions.get(node_name, ""),
                permissible=is_permissible,
                violation_rate=rate,
                flagged_count=flagged_count,
                scored_count=scored_count,
                examples=examples,
            )
        )
        for example in examples:
            key = (
                example.intervention_point,
                example.tool_name,
                example.target_value,
                example.behavior,
            )
            if key in seen_examples:
                continue
            flat_examples.append(example)
            seen_examples.add(key)

    return FindingsSummary(
        suite_id=suite_id,
        run_id=run_id,
        behavior_name=_taxonomy_behavior_name(taxonomy, suite_id),
        target=_first_nonempty(score_rows, "target"),
        overall=overall,
        behaviors=tuple(behavior_findings),
        failing_examples=tuple(flat_examples),
        min_rate=min_rate,
        min_count=min_count,
    )


def load_findings(
    run_dir: str | Path,
    *,
    min_rate: float = 0.0,
    min_count: int = 1,
    max_examples_per_behavior: int = 3,
) -> FindingsSummary:
    """Load ASSERT run artifacts from disk and summarize ACS-relevant findings."""
    resolved_run_dir = Path(run_dir).expanduser()
    scores_path = resolved_run_dir / SCORES_FILE
    if not scores_path.is_file():
        raise FileNotFoundError(f"ASSERT scores file not found: {scores_path}")

    taxonomy_path = resolved_run_dir.parent / "taxonomy.json"
    taxonomy = load_policy(taxonomy_path) if taxonomy_path.is_file() else None
    return summarize_findings(
        load_jsonl(scores_path),
        load_jsonl(resolved_run_dir / INFERENCE_SET_FILE),
        taxonomy,
        suite_id=resolved_run_dir.parent.name,
        run_id=resolved_run_dir.name,
        min_rate=min_rate,
        min_count=min_count,
        max_examples_per_behavior=max_examples_per_behavior,
    )


def _dimension_finding(
    rows: list[dict[str, Any]],
    dimension: str,
) -> DimensionFinding | None:
    summary = compute_dimension_summary(rows, dimension)
    rate = summary.get("rate")
    if not isinstance(rate, (int, float)):
        return None
    return DimensionFinding(
        name=dimension,
        rate=float(rate),
        flagged_count=int(summary.get("flagged_count") or 0),
        scored_count=int(summary.get("count") or 0),
    )


def _violated_nodes(row: dict[str, Any]) -> list[str]:
    """Taxonomy node names the judge marked violated for this row.

    This is the actually-violated taxonomy category, which is what should drive
    a guardrail's behavior/definition/permissibility, not the behavior the test
    case happened to be generated under (they can differ, and conflating them
    inverts policy intent when the test-case behavior is permissible but the
    violated node is not).
    """
    return [name for name, violated in _node_judgment_flags(row) if violated]


def _judged_nodes(row: dict[str, Any]) -> list[str]:
    """All taxonomy node names the judge evaluated for this row."""
    return [name for name, _ in _node_judgment_flags(row)]


def _node_judgment_flags(row: dict[str, Any]) -> list[tuple[str, bool]]:
    """Return (node_name, violated) for the RELEVANT node judgments of a row.

    ASSERT normalizes ``node_judgments`` into a dense list where every taxonomy
    node appears in every row, with ``relevant: false`` / ``violated: null`` for
    nodes that were not relevant to that test case (see
    ``assert_ai.core.judge_normalization._expand_sparse_node_judgments``). Only
    relevant entries are real judgments, so non-relevant placeholders are excluded
    here; otherwise per-node rate denominators would be inflated by every other
    category in the taxonomy. Sparse/legacy rows that omit the ``relevant`` field
    are treated as relevant.
    """
    verdict = row.get("verdict")
    if not isinstance(verdict, dict):
        return []
    node_judgments = verdict.get("node_judgments")
    if not isinstance(node_judgments, list):
        return []
    flags: list[tuple[str, bool]] = []
    for judgment in node_judgments:
        if not isinstance(judgment, dict):
            continue
        name = judgment.get("node_name")
        if not isinstance(name, str) or not name:
            continue
        if "relevant" in judgment and judgment.get("relevant") is not True:
            continue
        flags.append((name, judgment.get("violated") is True))
    return flags


def _attributed_violated_nodes(row: dict[str, Any]) -> tuple[set[str], set[str]]:
    """Return (violated_nodes, judged_nodes) for a row, with a fallback.

    When the row is flagged as a ``policy_violation`` but the judge marked no
    node violated (degenerate/legacy data), attribute it to the test-case
    behavior so the finding is not lost.
    """
    violated = set(_violated_nodes(row))
    judged = set(_judged_nodes(row))
    if not violated:
        flag = get_verdict_dimension(row.get("verdict"), "policy_violation")
        if is_valid_event_flag(flag) and flag:
            behavior = _score_row_behavior(row)
            if behavior:
                violated = {behavior}
                judged = judged | {behavior}
    return violated, judged


def _node_violation_metrics(
    scored_rows: list[dict[str, Any]],
) -> dict[str, dict[str, int | float]]:
    """Per-violated-node violation rate over the rows where the node was judged."""
    judged_counts: dict[str, int] = {}
    violated_counts: dict[str, int] = {}
    for row in scored_rows:
        violated, judged = _attributed_violated_nodes(row)
        for node in judged:
            judged_counts[node] = judged_counts.get(node, 0) + 1
        for node in violated:
            violated_counts[node] = violated_counts.get(node, 0) + 1

    metrics: dict[str, dict[str, int | float]] = {}
    for node, flagged in violated_counts.items():
        scored = judged_counts.get(node, flagged)
        if scored <= 0:
            continue
        metrics[node] = {
            "rate": flagged / scored,
            "flagged_count": flagged,
            "scored_count": scored,
        }
    return metrics


def _collect_node_examples(
    node_name: str,
    scored_rows: list[dict[str, Any]],
    inference_by_id: dict[str, dict[str, Any]],
    *,
    max_examples: int,
) -> list[FailingExample]:
    # Gather all candidate examples for this node, deduped. The dedup key includes
    # the tool name so two distinct tools that stringify to the same args/result
    # are not collapsed (which would drop a tool from the gated set).
    candidates: list[FailingExample] = []
    seen: set[tuple[str, str | None, str]] = set()
    for row in scored_rows:
        violated, _ = _attributed_violated_nodes(row)
        if node_name not in violated:
            continue
        test_case_id = str(row.get("test_case_id") or "")
        for example in _examples_from_row(
            inference_by_id.get(test_case_id),
            node_name=node_name,
            evidence_turns=_evidence_turns_for_node(row, node_name),
        ):
            key = (example.intervention_point, example.tool_name, example.target_value)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(example)

    # Coverage set: guarantee at least one example per distinct intervention point
    # and per distinct (tool point, tool_name). These drive the security-bearing
    # outputs (which tools the manifest gates, which points the policy guards), so
    # they must NOT be limited by the display-snippet budget; otherwise a violating
    # tool or a violated intervention point could be silently dropped.
    coverage: list[FailingExample] = []
    coverage_keys: set[tuple[str, str | None]] = set()
    for example in candidates:
        coverage_key = (example.intervention_point, example.tool_name)
        if coverage_key in coverage_keys:
            continue
        coverage_keys.add(coverage_key)
        coverage.append(example)

    # The cap only bounds the EXTRA representative snippets beyond full coverage.
    result = list(coverage)
    if len(result) < max_examples:
        for example in candidates:
            if example in result:
                continue
            result.append(example)
            if len(result) >= max_examples:
                break
    return result


def _examples_from_row(
    inference_row: dict[str, Any] | None,
    *,
    node_name: str,
    evidence_turns: list[int] | None = None,
) -> list[FailingExample]:
    """Recover the policy-target evidence for a violation from the transcript.

    Covers assistant outputs (``output``), tool calls and results
    (``pre_tool_call`` / ``post_tool_call``), and falls back to the user input
    (``input``) when no agent-side evidence is present.

    When the judge cited specific evidence turns for this violation, the assistant
    output(s) at those turns are used instead of the final assistant message; a
    multi-turn row may be judged unsafe on an earlier turn, and replaying the
    (possibly benign) final turn would give false validation confidence.
    """
    examples: list[FailingExample] = []

    cited_outputs = _cited_assistant_outputs(inference_row, evidence_turns)
    # When the judge cited specific evidence turns, trust them: use the cited
    # assistant output(s), and emit NO output example if the citation points only at
    # tool/user turns (a tool-only violation is captured by the tool-call paths).
    # Only fall back to the final assistant message when there are no citations at
    # all (legacy/sparse rows), preserving the prior behavior there.
    assistant_texts = cited_outputs if evidence_turns else _final_assistant_texts(inference_row)
    for assistant_text in assistant_texts:
        examples.append(
            FailingExample(
                intervention_point="output",
                snapshot={"output": assistant_text},
                target_value=assistant_text,
                behavior=node_name,
                dimension="policy_violation",
            )
        )

    for tool_name, tool_args, tool_result in _target_tool_calls(inference_row):
        examples.append(
            FailingExample(
                intervention_point="pre_tool_call",
                snapshot={"tool_call": {"name": tool_name, "args": tool_args}},
                target_value=_stringify(tool_args),
                behavior=node_name,
                dimension="policy_violation",
                tool_name=tool_name,
            )
        )
        if tool_result:
            examples.append(
                FailingExample(
                    intervention_point="post_tool_call",
                    snapshot={"tool_call": {"name": tool_name}, "tool_result": tool_result},
                    target_value=_stringify(tool_result),
                    behavior=node_name,
                    dimension="policy_violation",
                    tool_name=tool_name,
                )
            )

    if not examples:
        user_text = _final_message_for_role(inference_row, "user")
        if user_text:
            examples.append(
                FailingExample(
                    intervention_point="input",
                    snapshot={"input": user_text},
                    target_value=user_text,
                    behavior=node_name,
                    dimension="policy_violation",
                )
            )
    return examples


def _evidence_turns_for_node(row: dict[str, Any], node_name: str) -> list[int]:
    """The judge's cited evidence-turn indices for ``node_name`` in this row.

    These are 1-based indices into the target view's messages INCLUDING the system
    message: the judge builds its citation index map with ``skip_system=False``
    (assert_ai/stages/judge.py), so a leading system prompt occupies index 1. See
    ``assert_ai.core.judge_normalization``. Empty for legacy/sparse rows that carry
    no citations.
    """
    verdict = row.get("verdict")
    if not isinstance(verdict, dict):
        return []
    node_judgments = verdict.get("node_judgments")
    if not isinstance(node_judgments, list):
        return []
    turns: list[int] = []
    for judgment in node_judgments:
        if not isinstance(judgment, dict) or judgment.get("node_name") != node_name:
            continue
        raw = judgment.get("evidence_turns")
        if isinstance(raw, list):
            turns.extend(int(t) for t in raw if isinstance(t, int))
    return turns


def _cited_assistant_outputs(
    inference_row: dict[str, Any] | None,
    evidence_turns: list[int] | None,
) -> list[str]:
    """Assistant message text at the judge's cited evidence turns, in order.

    ``evidence_turns`` are 1-based indices into the target view's messages with the
    system message INCLUDED, because the judge enumerates citations with
    ``skip_system=False`` (assert_ai/stages/judge.py). The full view list is indexed
    here without stripping system, so the indices line up exactly; the
    ``role == "assistant"`` guard then keeps only assistant turns (non-assistant
    cited turns are handled by the input/tool-call paths).
    """
    if not evidence_turns or not isinstance(inference_row, dict):
        return []
    events = inference_row.get("events")
    if not isinstance(events, list):
        return []
    messages = _collect_messages_for_view(events, "target")
    outputs: list[str] = []
    seen: set[str] = set()
    for turn in evidence_turns:
        index = turn - 1
        if not 0 <= index < len(messages):
            continue
        message = messages[index]
        content = message.content
        if message.role == "assistant" and isinstance(content, str) and content and content not in seen:
            seen.add(content)
            outputs.append(content)
    return outputs


def _final_assistant_texts(inference_row: dict[str, Any] | None) -> list[str]:
    """The final assistant message as a single-item list, or empty.

    Fallback when the judge cited no evidence turns (legacy/sparse data); preserves
    the prior behavior of validating against the last assistant output.
    """
    assistant_text = _final_message_for_role(inference_row, "assistant")
    return [assistant_text] if assistant_text else []


def _target_tool_calls(
    inference_row: dict[str, Any] | None,
) -> list[tuple[str, dict[str, Any], str]]:
    """Return (tool_name, tool_args, tool_result) for every target tool call.

    A row is flagged at the node level with no mapping to a specific tool call, so
    every tool call the target made is a candidate for the guardrail. Returning
    all of them (rather than only the last) ensures the actually-violating tool is
    gated, not just whichever ran last. Order is preserved; duplicates are kept so
    the caller's dedup can collapse identical calls.
    """
    if not isinstance(inference_row, dict):
        return []
    events = inference_row.get("events")
    if not isinstance(events, list):
        return []

    calls: list[tuple[str, dict[str, Any], str]] = []
    for event in events:
        if not isinstance(event, dict) or "target" not in _event_views(event.get("view")):
            continue
        edit = event.get("edit")
        if not isinstance(edit, dict) or edit.get("type") != "tool_call":
            continue
        name = edit.get("tool_name")
        if not isinstance(name, str) or not name:
            continue
        raw_args = edit.get("tool_args")
        args = raw_args if isinstance(raw_args, dict) else {}
        raw_result = edit.get("tool_result")
        result = raw_result if isinstance(raw_result, str) else ""
        calls.append((name, args, result))
    return calls


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return str(value)


def _final_message_for_role(
    inference_row: dict[str, Any] | None,
    role: str,
) -> str:
    if not isinstance(inference_row, dict):
        return ""

    final_text = ""
    events = inference_row.get("events")
    if not isinstance(events, list):
        return ""

    for event in events:
        if not isinstance(event, dict) or "target" not in _event_views(event.get("view")):
            continue
        edit = event.get("edit")
        if not isinstance(edit, dict) or edit.get("type") != "add_message":
            continue
        message = edit.get("message")
        if not isinstance(message, dict) or message.get("role") != role:
            continue
        content = message.get("content")
        if isinstance(content, str) and content:
            final_text = content
    return final_text


def _event_views(value: Any) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    if value is None:
        return ()
    return (str(value),)


def _index_inference_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        test_case_id = str(row.get("test_case_id") or "")
        if test_case_id and test_case_id not in indexed:
            indexed[test_case_id] = row
    return indexed


def _score_row_behavior(row: dict[str, Any]) -> str:
    return row_behavior(row) or str(row.get("behavior") or "")


def _taxonomy_behavior_name(taxonomy: dict[str, Any] | None, fallback: str) -> str:
    behavior = (taxonomy or {}).get("behavior")
    if isinstance(behavior, dict):
        name = behavior.get("name")
        if isinstance(name, str) and name:
            return name
    return fallback


def _first_nonempty(rows: list[dict[str, Any]], key: str) -> str:
    for row in rows:
        value = row.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


__all__ = [
    "BehaviorFinding",
    "DimensionFinding",
    "FailingExample",
    "FindingsSummary",
    "load_findings",
    "summarize_findings",
]
