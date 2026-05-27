# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Transcript-specific normalization for judge verdicts."""

from __future__ import annotations

import re
from typing import Any, Callable

from p2m.core.judge_citations import (
    CITATION_SOURCE_KINDS,
    MAX_CANONICAL_CITATION_PART_CHARS,
    MAX_FULL_MESSAGE_CITATION_CHARS,
    RESOLVED_CITATION_METHODS,
)
from p2m.core.transcript import Transcript

DIMENSIONS_KEY = "dimensions"
NODE_JUDGMENTS_KEY = "node_judgments"
INLINE_CITATION_PATTERN = re.compile(r"\[(\d+)\]")
CONFIDENCE_LEVELS = ("high", "medium", "low")


def _normalize_transcript_judge_verdict_impl(
    verdict: dict[str, Any],
    *,
    transcript: Transcript,
    index_to_message_id: dict[str, str],
    score_keys: list[str],
    policy_raw: dict[str, Any],
    extract_xml_citations_fn: Callable[..., list[dict[str, Any]]],
    summary_justification_fn: Callable[[dict[str, Any], list[str]], str],
) -> tuple[dict[str, Any] | None, str | None]:
    normalized = dict(verdict)
    highlights = str(normalized.pop("highlights", "") or "")
    citation_warnings: list[str] = []
    judgment_warnings: list[str] = []

    citations = extract_xml_citations_fn(
        highlights,
        index_to_message_id,
        transcript,
        view="target",
    )
    citation_indices: set[int] = set()
    citation_by_index: dict[int, dict[str, Any]] = {}
    valid_citations: list[dict[str, Any]] = []
    for citation in citations:
        citation_index = citation.get("index")
        if not isinstance(citation_index, int):
            citation_warnings.append(_citation_warning("invalid_citation_index"))
            continue
        if citation_index in citation_indices:
            citation_warnings.append(_citation_warning("duplicate_citation_index", citation_index))
            continue
        parts = citation.get("parts")
        if not isinstance(parts, list) or not parts:
            citation_warnings.append(_citation_warning("missing_citation_parts", citation_index))
            continue
        parts_ok = True
        for part in parts:
            if not isinstance(part, dict):
                citation_warnings.append(_citation_warning("invalid_citation_part", citation_index))
                parts_ok = False
                break
            resolution = part.get("resolution")
            if not isinstance(resolution, dict):
                citation_warnings.append(_citation_warning("invalid_citation_resolution", citation_index))
                parts_ok = False
                break
        if not parts_ok:
            continue
        citation_indices.add(citation_index)
        citation_by_index[citation_index] = citation
        valid_citations.append(citation)
    if not valid_citations:
        citation_warnings.append("missing_citations")

    dimensions = normalized.get(DIMENSIONS_KEY)
    if not isinstance(dimensions, dict):
        return None, "missing_dimensions"
    for key in score_keys:
        if not isinstance(dimensions.get(key), bool):
            return None, f"invalid_dimension:{key}"

    dimension_justifications = normalized.get("dimension_justifications")
    if not isinstance(dimension_justifications, dict):
        return None, "missing_dimension_justifications"
    for key in score_keys:
        value = dimension_justifications.get(key)
        if not isinstance(value, str) or not value.strip():
            return None, f"missing_dimension_justification:{key}"

    raw_node_judgments = normalized.get(NODE_JUDGMENTS_KEY)
    if not isinstance(raw_node_judgments, list):
        return None, "missing_node_judgments"

    explanation_texts = [
        value
        for value in dimension_justifications.values()
        if isinstance(value, str) and value.strip()
    ]
    raw_justification = normalized.get("justification")
    if isinstance(raw_justification, str) and raw_justification.strip():
        explanation_texts.append(raw_justification)

    behavior_categories = policy_raw.get("behavior_categories")
    if not isinstance(behavior_categories, list):
        return None, "missing_policy_behavior_categories"
    name_to_index: dict[str, int] = {}
    for idx, entry in enumerate(behavior_categories):
        if isinstance(entry, dict) and isinstance(entry.get("name"), str):
            stripped = entry["name"].strip()
            if stripped:
                name_to_index.setdefault(stripped, idx)

    sparse_nodes: list[dict[str, Any]] = []
    seen_node_indices: set[int] = set()
    for node in raw_node_judgments:
        if not isinstance(node, dict):
            return None, "invalid_node_judgment"
        node_name_raw = node.get("node_name")
        violated = node.get("violated")
        confidence = node.get("confidence")
        reasoning = node.get("reasoning")
        if not isinstance(node_name_raw, str) or not node_name_raw.strip():
            return None, "invalid_node_name"
        node_name = node_name_raw.strip()
        if node_name not in name_to_index:
            return None, "unknown_node_name"
        node_index = name_to_index[node_name]
        if node_index in seen_node_indices:
            judgment_warnings.append(f"duplicate_node_name:{node_name}")
            continue
        if not isinstance(violated, bool):
            return None, "invalid_node_violated"
        if not _is_valid_confidence_label(confidence):
            return None, "invalid_node_confidence"
        if not isinstance(reasoning, str) or not reasoning.strip():
            return None, "missing_node_reasoning"
        sparse_node = {
            "node_index": node_index,
            "violated": violated,
            "confidence": confidence,
            "reasoning": reasoning.strip(),
        }
        sparse_nodes.append(sparse_node)
        seen_node_indices.add(node_index)
        explanation_texts.append(sparse_node["reasoning"])

    referenced_citation_indices: set[int] = set()
    if citation_indices:
        for text in explanation_texts:
            markers = _extract_inline_citation_indices(text)
            if not markers:
                judgment_warnings.append("missing_inline_citation_marker")
                continue
            dangling = [marker for marker in markers if marker not in citation_indices]
            if dangling:
                judgment_warnings.append("dangling_inline_citation_marker")
            referenced_citation_indices.update(marker for marker in markers if marker in citation_indices)

    message_text_by_id = _collect_target_message_text_by_id(transcript)
    for citation_index in referenced_citation_indices:
        citation = citation_by_index.get(citation_index)
        if not isinstance(citation, dict):
            citation_warnings.append(_citation_warning("missing_referenced_citation", citation_index))
            continue
        parts = citation.get("parts")
        if not isinstance(parts, list) or not parts:
            citation_warnings.append(_citation_warning("missing_citation_parts", citation_index))
            continue
        for part in parts:
            if not isinstance(part, dict):
                citation_warnings.append(_citation_warning("invalid_citation_part", citation_index))
                continue
            claimed_message_index = part.get("claimed_message_index")
            if not isinstance(claimed_message_index, str) or not claimed_message_index:
                citation_warnings.append(_citation_warning("missing_claimed_message_index", citation_index))
                continue
            message_id = part.get("message_id")
            if not isinstance(message_id, str) or not message_id:
                citation_warnings.append(_citation_warning("invalid_citation_message_id", citation_index))
                continue
            resolution = part.get("resolution")
            if not isinstance(resolution, dict):
                citation_warnings.append(_citation_warning("invalid_citation_resolution", citation_index))
                continue
            if resolution.get("status") == "ambiguous":
                citation_warnings.append(_citation_warning("ambiguous_citation_part", citation_index))
                continue
            if resolution.get("status") != "resolved":
                citation_warnings.append(_citation_warning("unresolved_citation_part", citation_index))
                continue
            if resolution.get("method") not in RESOLVED_CITATION_METHODS:
                citation_warnings.append(_citation_warning("noncanonical_citation_method", citation_index))
                continue
            source_kind = part.get("source_kind")
            if source_kind is not None and source_kind not in CITATION_SOURCE_KINDS:
                citation_warnings.append(_citation_warning("invalid_citation_source_kind", citation_index))
                continue
            tool_call_id = part.get("tool_call_id")
            if tool_call_id is not None and (not isinstance(tool_call_id, str) or not tool_call_id):
                citation_warnings.append(_citation_warning("invalid_citation_tool_call_id", citation_index))
                continue
            tool_arg = part.get("tool_arg")
            if tool_arg is not None and (not isinstance(tool_arg, str) or not tool_arg):
                citation_warnings.append(_citation_warning("invalid_citation_tool_arg", citation_index))
                continue
            if source_kind == "tool_arg" and not (isinstance(tool_arg, str) and tool_arg):
                citation_warnings.append(_citation_warning("missing_citation_tool_arg", citation_index))
                continue
            matched_message_index = part.get("matched_message_index")
            if not isinstance(matched_message_index, str) or not matched_message_index:
                citation_warnings.append(_citation_warning("missing_matched_message_index", citation_index))
                continue
            position = part.get("position")
            if (
                not isinstance(position, list)
                or len(position) != 2
                or not all(isinstance(value, int) for value in position)
            ):
                citation_warnings.append(_citation_warning("invalid_citation_position", citation_index))
                continue
            if _citation_part_is_overscoped(part, message_text_by_id=message_text_by_id):
                citation_warnings.append(_citation_warning("overscoped_citation_part", citation_index))

    dense_node_judgments = _expand_sparse_node_judgments(
        sparse_nodes,
        behavior_categories=behavior_categories,
        citation_by_index=citation_by_index,
    )

    violated_nodes = sum(
        1
        for node in dense_node_judgments
        if node.get("relevant") is True and node.get("violated") is True
    )
    policy_violation = dimensions.get("policy_violation")
    if policy_violation is True and violated_nodes == 0:
        judgment_warnings.append("policy_violation_without_violated_node")
        dimensions["policy_violation"] = False
    if policy_violation is False and violated_nodes > 0:
        judgment_warnings.append("violated_node_without_policy_violation")
        dimensions["policy_violation"] = True

    normalized[NODE_JUDGMENTS_KEY] = dense_node_judgments
    normalized["justification"] = (
        raw_justification.strip()
        if isinstance(raw_justification, str) and raw_justification.strip()
        else summary_justification_fn(normalized, score_keys)
    )
    normalized["citations"] = valid_citations
    if citation_warnings:
        normalized["citation_warnings"] = sorted(set(citation_warnings))
    if judgment_warnings:
        normalized["judgment_warnings"] = sorted(set(judgment_warnings))
    return normalized, None


def _is_valid_confidence_label(value: Any) -> bool:
    return isinstance(value, str) and value in CONFIDENCE_LEVELS


def _citation_warning(code: str, citation_index: int | None = None) -> str:
    if citation_index is None:
        return code
    return f"citation_{citation_index}:{code}"


def _extract_inline_citation_indices(text: str) -> list[int]:
    return [int(match.group(1)) for match in INLINE_CITATION_PATTERN.finditer(text)]


def _collect_target_message_text_by_id(transcript: Transcript) -> dict[str, str]:
    return {
        message_id: message.content
        for message_id, message in transcript.collect_messages_with_ids("target")
    }


def _citation_turns_for_indices(
    citation_indices: list[int],
    citation_by_index: dict[int, dict[str, Any]],
) -> list[int]:
    turns: set[int] = set()
    for citation_index in citation_indices:
        citation = citation_by_index.get(citation_index)
        if not isinstance(citation, dict):
            continue
        parts = citation.get("parts")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict):
                continue
            matched_message_index = part.get("matched_message_index")
            if isinstance(matched_message_index, str) and matched_message_index.isdigit():
                turns.add(int(matched_message_index))
    return sorted(turns)


def _behavior_name(behavior_categories: list[Any], node_index: int) -> str:
    behavior = behavior_categories[node_index] if 0 <= node_index < len(behavior_categories) else None
    if isinstance(behavior, dict) and isinstance(behavior.get("name"), str):
        return str(behavior["name"]).strip()
    return ""


def _expand_sparse_node_judgments(
    raw_nodes: list[dict[str, Any]],
    *,
    behavior_categories: list[Any],
    citation_by_index: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    dense_nodes = [
        {
            "node_index": index,
            "node_name": _behavior_name(behavior_categories, index),
            "relevant": False,
            "violated": None,
            "confidence": None,
            "evidence_turns": [],
            "reasoning": "",
        }
        for index in range(len(behavior_categories))
    ]
    for node in raw_nodes:
        node_index = int(node["node_index"])
        reasoning = str(node["reasoning"]).strip()
        dense_nodes[node_index] = {
            "node_index": node_index,
            "node_name": _behavior_name(behavior_categories, node_index),
            "relevant": True,
            "violated": bool(node["violated"]),
            "confidence": str(node["confidence"]),
            "evidence_turns": _citation_turns_for_indices(
                _extract_inline_citation_indices(reasoning),
                citation_by_index,
            ),
            "reasoning": reasoning,
        }
    return dense_nodes


def _citation_part_is_overscoped(
    part: dict[str, Any],
    *,
    message_text_by_id: dict[str, str],
) -> bool:
    message_id = part.get("message_id")
    position = part.get("position")
    if not isinstance(message_id, str) or not message_id:
        return False
    if not (
        isinstance(position, list)
        and len(position) == 2
        and all(isinstance(value, int) for value in position)
    ):
        return False
    message_text = message_text_by_id.get(message_id, "")
    if not message_text:
        return False
    start, end = position
    span_len = end - start
    message_len = len(message_text)
    if span_len <= 0 or message_len <= 0:
        return False
    if span_len == message_len and message_len > MAX_FULL_MESSAGE_CITATION_CHARS:
        return True
    return (
        span_len > MAX_CANONICAL_CITATION_PART_CHARS
        and message_len > MAX_FULL_MESSAGE_CITATION_CHARS
    )
