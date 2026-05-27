# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Citation extraction and repair helpers for transcript judge outputs."""

from __future__ import annotations

from dataclasses import dataclass, field
import html
import json
import re
from typing import Any, Dict, List, Literal

from rapidfuzz import fuzz

from p2m.core.transcript import SearchableMessageEntry, Transcript

ANCHOR_CONTEXT_CHARS = 24
NORMALIZED_QUOTE_CHARS = {
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
}

NEARBY_CITATION_MESSAGE_DISTANCE = 2
MIN_FUZZY_CITATION_CHARS = 16
MIN_FUZZY_CITATION_SCORE = 88.0
MIN_FUZZY_CITATION_MARGIN = 3.0
FUZZY_BOUNDARY_SCORE_TOLERANCE = 1.5
MAX_FUZZY_BOUNDARY_EXTENSION_CHARS = 48
MAX_AUTO_SPLIT_CITATION_PART_CHARS = 320
MAX_CANONICAL_CITATION_PART_CHARS = 320
MAX_FULL_MESSAGE_CITATION_CHARS = 240

CITATION_SOURCE_KINDS = frozenset({"message", "tool_arg", "tool_result"})
RESOLVED_CITATION_METHODS = frozenset(
    {
        "exact",
        "conservative_fuzzy",
    }
)

# Single source of truth for XML citation format.
CITE_XML_EXAMPLE = '1. <cite id="3" description="Key evidence">exact text from XML message 3</cite>'
CITE_XML_PATTERN = re.compile(
    r'(\d+)\.\s*<cite id="([^"]+)" description="([^"]*)">(.*?)(?:</cite>|(?=\n\s*\d+\.\s*<cite id=")|$)',
    re.DOTALL,
)


@dataclass(frozen=True)
class _CitationCandidate:
    index: str
    message_id: str
    message_text: str
    tool_call_id: str | None = None
    tool_args: dict[str, str] = field(default_factory=dict)
    tool_result: str = ""


@dataclass(frozen=True)
class _CitationSource:
    source_kind: Literal["message", "tool_arg", "tool_result"]
    text: str
    tool_arg: str | None = None


@dataclass(frozen=True)
class _CandidateSourceMatch:
    candidate: _CitationCandidate
    source: _CitationSource
    position: tuple[int, int]


def extract_xml_citations(
    highlights: str,
    index_to_message_id: Dict[str, str],
    transcript: Transcript,
    *,
    view: str = "target",
) -> List[Dict[str, Any]]:
    """Resolve XML citations to stable message IDs and auditable message spans."""
    if not highlights:
        return []

    searchable_entries_by_id = {
        entry.message_id: entry
        for entry in transcript.collect_searchable_messages_with_ids(view)
    }
    ordered_candidates = [
        _candidate_from_searchable_entry(
            message_index,
            message_id,
            searchable_entries_by_id.get(message_id),
        )
        for message_index, message_id in index_to_message_id.items()
    ]
    citations: List[Dict[str, Any]] = []

    for match in CITE_XML_PATTERN.finditer(highlights):
        citation_index = int(match.group(1))
        claimed_index = match.group(2)
        description = html.unescape(match.group(3).strip())
        quoted_text = html.unescape(match.group(4).strip())

        parts = [
            _resolve_citation_part(
                claimed_index,
                index_to_message_id,
                ordered_candidates,
                raw_part,
            )
            for raw_part in _split_citation_quote_parts(quoted_text)
        ]
        parts = _coerce_citation_parts_to_single_message(claimed_index, parts)

        citations.append(
            {
                "index": citation_index,
                "description": description,
                "parts": parts,
            }
        )

    return citations


def _candidate_from_searchable_entry(
    message_index: str,
    message_id: str,
    entry: SearchableMessageEntry | None,
) -> _CitationCandidate:
    if entry is None:
        return _CitationCandidate(index=message_index, message_id=message_id, message_text="")
    return _CitationCandidate(
        index=message_index,
        message_id=message_id,
        message_text=entry.message.content,
        tool_call_id=entry.tool_call_id,
        tool_args={
            name: _stringify_tool_arg_value(value)
            for name, value in entry.tool_args.items()
        },
        tool_result=entry.tool_result,
    )


def _stringify_tool_arg_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _coerce_citation_parts_to_single_message(
    claimed_message_index: str,
    parts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    resolved_targets = {
        (str(part.get("matched_message_index") or ""), str(part.get("message_id") or ""))
        for part in parts
        if isinstance(part, dict)
        and isinstance(part.get("resolution"), dict)
        and part["resolution"].get("status") == "resolved"
        and part.get("matched_message_index")
        and part.get("message_id")
    }
    if len(resolved_targets) <= 1:
        return parts

    detail = "Citation parts resolved to multiple transcript messages. One citation must stay within one XML message."
    coerced_parts: list[dict[str, Any]] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        anchor = part.get("anchor")
        quoted_text = str(part.get("quoted_text") or "")
        if not isinstance(anchor, dict):
            anchor = {"exact": quoted_text}
        degraded: dict[str, Any] = {
            "claimed_message_index": claimed_message_index,
            "message_id": part.get("message_id") if isinstance(part.get("message_id"), str) else "",
            "quoted_text": quoted_text,
            "position": None,
            "anchor": anchor,
            "resolution": {
                "status": "ambiguous",
                "method": "ambiguous_quote_match",
                "detail": detail,
            },
        }
        source_kind = part.get("source_kind")
        if source_kind in CITATION_SOURCE_KINDS:
            degraded["source_kind"] = source_kind
        tool_call_id = part.get("tool_call_id")
        if isinstance(tool_call_id, str) and tool_call_id:
            degraded["tool_call_id"] = tool_call_id
        tool_arg = part.get("tool_arg")
        if isinstance(tool_arg, str) and tool_arg:
            degraded["tool_arg"] = tool_arg
        coerced_parts.append(degraded)
    return coerced_parts


def _split_citation_quote_parts(quoted_text: str) -> list[str]:
    if not quoted_text:
        return [quoted_text]
    if "[...]" in quoted_text:
        return [part.strip() for part in quoted_text.split("[...]") if part.strip()] or [quoted_text]
    if len(quoted_text) <= MAX_AUTO_SPLIT_CITATION_PART_CHARS:
        return [quoted_text]

    segments: list[str] = []
    for block in re.split(r"\n\s*\n", quoted_text):
        stripped_block = block.strip()
        if not stripped_block:
            continue
        lines = [line.strip() for line in stripped_block.splitlines() if line.strip()]
        has_list_lines = any(line.startswith(("-", "*")) or re.match(r"^\d+[.)]\s", line) for line in lines)
        if len(lines) > 1 and has_list_lines:
            segments.extend(lines)
            continue
        if len(stripped_block) <= MAX_AUTO_SPLIT_CITATION_PART_CHARS:
            segments.append(stripped_block)
            continue
        sentences = [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", stripped_block) if sentence.strip()]
        if len(sentences) <= 1:
            segments.append(stripped_block)
            continue
        current = ""
        for sentence in sentences:
            candidate = sentence if not current else f"{current} {sentence}"
            if len(candidate) <= MAX_AUTO_SPLIT_CITATION_PART_CHARS:
                current = candidate
                continue
            if current:
                segments.append(current)
            current = sentence
        if current:
            segments.append(current)

    filtered = [segment for segment in segments if segment]
    return filtered or [quoted_text]


def _resolve_citation_part(
    claimed_message_index: str,
    index_to_message_id: Dict[str, str],
    ordered_candidates: list[_CitationCandidate],
    quoted_text: str,
) -> dict[str, Any]:
    """Resolve one citation part against the transcript with Petri-style repair tiers."""
    anchor = {"exact": quoted_text}
    claimed_message_id = index_to_message_id.get(claimed_message_index, "")
    claimed_candidate, search_order = _partition_citation_candidates(
        claimed_message_index,
        ordered_candidates,
    )

    unresolved_method: Literal[
        "missing_message_id",
        "missing_message_text",
        "quote_not_found",
        "ambiguous_quote_match",
    ] = "quote_not_found"
    unresolved_detail = (
        "Quoted text did not match any transcript source after exact, normalized, and conservative fuzzy resolution."
    )
    if not claimed_message_id:
        unresolved_method = "missing_message_id"
        unresolved_detail = "Citation did not reference a transcript message, and no repair candidate could be resolved."
    elif claimed_candidate is None or not claimed_candidate.message_text:
        unresolved_method = "missing_message_text"
        unresolved_detail = "Transcript message text was empty or unavailable, and no repair candidate could be resolved."

    search_phases: list[Any] = [
        _find_all_exact_spans,
        _find_all_normalized_spans,
    ]

    for matcher in search_phases:
        outcome = _search_candidate_group(claimed_message_index, search_order, quoted_text, matcher)
        if outcome is None:
            continue
        status, match, detail = outcome
        if status == "resolved":
            return _build_resolved_citation_part(
                claimed_message_index,
                quoted_text,
                match,
                "exact",
                detail=detail,
            )
        return _build_unresolved_citation_part(
            claimed_message_index,
            claimed_message_id or match.candidate.message_id,
            quoted_text,
            anchor,
            status="ambiguous",
            method="ambiguous_quote_match",
            detail=detail,
        )

    fuzzy_outcome = _search_candidates_fuzzy(claimed_message_index, search_order, quoted_text)
    if fuzzy_outcome is not None:
        status, match, detail = fuzzy_outcome
        if status == "resolved":
            return _build_resolved_citation_part(
                claimed_message_index,
                quoted_text,
                match,
                "conservative_fuzzy",
                detail=detail,
            )
        return _build_unresolved_citation_part(
            claimed_message_index,
            claimed_message_id or match.candidate.message_id,
            quoted_text,
            anchor,
            status="ambiguous",
            method="ambiguous_quote_match",
            detail=detail,
        )

    return _build_unresolved_citation_part(
        claimed_message_index,
        claimed_message_id,
        quoted_text,
        anchor,
        status="unresolved",
        method=unresolved_method,
        detail=unresolved_detail,
    )


def _partition_citation_candidates(
    claimed_message_index: str,
    ordered_candidates: list[_CitationCandidate],
) -> tuple[_CitationCandidate | None, list[_CitationCandidate]]:
    search_order = _build_citation_search_order(claimed_message_index, ordered_candidates)
    claimed_candidate = next((candidate for candidate in search_order if candidate.index == claimed_message_index), None)
    return claimed_candidate, search_order


def _build_citation_search_order(
    claimed_message_index: str,
    ordered_candidates: list[_CitationCandidate],
) -> list[_CitationCandidate]:
    by_index = {candidate.index: candidate for candidate in ordered_candidates}
    ordered: list[_CitationCandidate] = []
    seen: set[str] = set()

    def append(index: str) -> None:
        candidate = by_index.get(index)
        if candidate is None or index in seen:
            return
        ordered.append(candidate)
        seen.add(index)

    append(claimed_message_index)
    if claimed_message_index.isdigit():
        base_index = int(claimed_message_index)
        numeric_indices = [int(candidate.index) for candidate in ordered_candidates if candidate.index.isdigit()]
        if numeric_indices:
            max_delta = max(abs(index - base_index) for index in numeric_indices)
            for delta in range(1, max_delta + 1):
                append(str(base_index - delta))
                append(str(base_index + delta))

    for candidate in ordered_candidates:
        append(candidate.index)

    return ordered


def _candidate_specific_sources(candidate: _CitationCandidate) -> list[_CitationSource]:
    sources: list[_CitationSource] = []
    for tool_arg, text in candidate.tool_args.items():
        if text:
            sources.append(_CitationSource(source_kind="tool_arg", text=text, tool_arg=tool_arg))
    if candidate.tool_result:
        sources.append(_CitationSource(source_kind="tool_result", text=candidate.tool_result))
    return sources


def _candidate_message_source(candidate: _CitationCandidate) -> _CitationSource:
    return _CitationSource(source_kind="message", text=candidate.message_text)


def _citation_source_label(source: _CitationSource) -> str:
    if source.source_kind == "tool_arg" and source.tool_arg:
        return f"tool argument '{source.tool_arg}'"
    if source.source_kind == "tool_result":
        return "tool result"
    return "message text"


def _search_candidate_group(
    claimed_message_index: str,
    candidates: list[_CitationCandidate],
    quoted_text: str,
    matcher: Any,
) -> tuple[Literal["resolved", "ambiguous"], _CandidateSourceMatch, str | None] | None:
    resolved_matches: list[tuple[_CandidateSourceMatch, str | None]] = []
    for candidate in candidates:
        outcome = _search_single_candidate(claimed_message_index, candidate, quoted_text, matcher)
        if outcome is None:
            continue
        status, match, detail = outcome
        if status == "ambiguous":
            return status, match, detail
        resolved_matches.append((match, detail))

    if not resolved_matches:
        return None
    if len(resolved_matches) > 1:
        detail = "Quoted text matched multiple transcript messages in the same repair tier."
        return "ambiguous", resolved_matches[0][0], detail

    return "resolved", resolved_matches[0][0], resolved_matches[0][1]


def _search_single_candidate(
    claimed_message_index: str,
    candidate: _CitationCandidate,
    quoted_text: str,
    matcher: Any,
) -> tuple[Literal["resolved", "ambiguous"], _CandidateSourceMatch, str | None] | None:
    specific_sources = _candidate_specific_sources(candidate)
    if specific_sources:
        outcome = _search_candidate_sources(claimed_message_index, candidate, specific_sources, quoted_text, matcher)
        if outcome is not None:
            return outcome
    return _search_candidate_sources(claimed_message_index, candidate, [_candidate_message_source(candidate)], quoted_text, matcher)


def _search_candidate_sources(
    claimed_message_index: str,
    candidate: _CitationCandidate,
    sources: list[_CitationSource],
    quoted_text: str,
    matcher: Any,
) -> tuple[Literal["resolved", "ambiguous"], _CandidateSourceMatch, str | None] | None:
    resolved_matches: list[_CandidateSourceMatch] = []
    for source in sources:
        matches = matcher(source.text, quoted_text)
        if len(matches) > 1:
            detail = f"Quoted text matched multiple spans in {_citation_source_label(source)} of XML message {candidate.index}."
            return "ambiguous", _CandidateSourceMatch(candidate, source, matches[0]), detail
        if len(matches) == 1:
            resolved_matches.append(_CandidateSourceMatch(candidate, source, matches[0]))

    if not resolved_matches:
        return None
    if len(resolved_matches) > 1:
        detail = f"Quoted text matched multiple sources in XML message {candidate.index}."
        return "ambiguous", resolved_matches[0], detail

    detail = _resolved_citation_detail(claimed_message_index, candidate.index)
    return "resolved", resolved_matches[0], detail


def _search_candidates_fuzzy(
    claimed_message_index: str,
    candidates: list[_CitationCandidate],
    quoted_text: str,
) -> tuple[Literal["resolved", "ambiguous"], _CandidateSourceMatch, str] | None:
    best_score = 0.0
    second_best_score = 0.0
    best_match: _CandidateSourceMatch | None = None

    for candidate in candidates:
        result = _find_best_candidate_fuzzy_match(candidate, quoted_text)
        if result is None:
            continue
        score, match, runner_up_score = result
        if score > best_score:
            second_best_score = max(second_best_score, best_score, runner_up_score)
            best_score = score
            best_match = match
        else:
            second_best_score = max(second_best_score, score)
        if best_match is match:
            second_best_score = max(second_best_score, runner_up_score)

    if best_match is None:
        return None
    if best_score < MIN_FUZZY_CITATION_SCORE:
        return None
    if best_score - second_best_score < MIN_FUZZY_CITATION_MARGIN:
        detail = (
            f"Conservative fuzzy repair found competing candidates within {MIN_FUZZY_CITATION_MARGIN:.1f} of the best score."
        )
        return "ambiguous", best_match, detail

    detail = (
        f"Resolved by conservative fuzzy repair from claimed XML message {claimed_message_index} to XML message {best_match.candidate.index}."
        if best_match.candidate.index != claimed_message_index
        else "Resolved by conservative fuzzy repair within the claimed XML message."
    )
    return "resolved", best_match, detail


def _find_best_candidate_fuzzy_match(
    candidate: _CitationCandidate,
    quoted_text: str,
) -> tuple[float, _CandidateSourceMatch, float] | None:
    specific_sources = _candidate_specific_sources(candidate)
    if specific_sources:
        result = _find_best_fuzzy_match_in_sources(candidate, specific_sources, quoted_text)
        if result is not None:
            return result
    return _find_best_fuzzy_match_in_sources(candidate, [_candidate_message_source(candidate)], quoted_text)


def _find_best_fuzzy_match_in_sources(
    candidate: _CitationCandidate,
    sources: list[_CitationSource],
    quoted_text: str,
) -> tuple[float, _CandidateSourceMatch, float] | None:
    best_score = 0.0
    second_best_score = 0.0
    best_match: _CandidateSourceMatch | None = None

    for source in sources:
        result = _find_best_fuzzy_span(
            source.text,
            quoted_text,
            source_kind=source.source_kind,
        )
        if result is None:
            continue
        score, position, runner_up_score = result
        match = _CandidateSourceMatch(candidate, source, position)
        if score > best_score:
            second_best_score = max(second_best_score, best_score, runner_up_score)
            best_score = score
            best_match = match
        else:
            second_best_score = max(second_best_score, score)

    if best_match is None:
        return None
    return best_score, best_match, second_best_score


def _resolved_citation_detail(
    claimed_message_index: str,
    matched_message_index: str,
) -> str | None:
    if not claimed_message_index or not matched_message_index or claimed_message_index == matched_message_index:
        return None
    return f"Resolved from claimed XML message {claimed_message_index} to XML message {matched_message_index}."


def _build_resolved_citation_part(
    claimed_message_index: str,
    quoted_text: str,
    match: _CandidateSourceMatch,
    method: Literal[
        "exact",
        "conservative_fuzzy",
    ],
    *,
    detail: str | None = None,
) -> dict[str, Any]:
    source_text = match.source.text
    start, end = match.position
    matched_text = source_text[start:end]
    anchor = _build_anchor(source_text, matched_text, match.position)
    resolved_detail = detail if detail is not None else _resolved_citation_detail(claimed_message_index, match.candidate.index)
    resolution: dict[str, Any] = {
        "status": "resolved",
        "method": method,
    }
    if resolved_detail:
        resolution["detail"] = resolved_detail
    payload: dict[str, Any] = {
        "claimed_message_index": claimed_message_index,
        "matched_message_index": match.candidate.index,
        "message_id": match.candidate.message_id,
        "quoted_text": quoted_text,
        "position": [start, end],
        "anchor": anchor,
        "source_kind": match.source.source_kind,
        "resolution": resolution,
    }
    if match.candidate.tool_call_id:
        payload["tool_call_id"] = match.candidate.tool_call_id
    if match.source.tool_arg:
        payload["tool_arg"] = match.source.tool_arg
    return payload


def _build_unresolved_citation_part(
    claimed_message_index: str,
    message_id: str,
    quoted_text: str,
    anchor: dict[str, Any],
    *,
    status: Literal["unresolved", "ambiguous"],
    method: Literal[
        "missing_message_id",
        "missing_message_text",
        "quote_not_found",
        "ambiguous_quote_match",
    ],
    detail: str,
) -> dict[str, Any]:
    return {
        "claimed_message_index": claimed_message_index,
        "message_id": message_id,
        "quoted_text": quoted_text,
        "position": None,
        "anchor": anchor,
        "resolution": {
            "status": status,
            "method": method,
            "detail": detail,
        },
    }


def _build_anchor(message_text: str, quoted_text: str, position: tuple[int, int]) -> dict[str, Any]:
    start, end = position
    prefix_start = max(0, start - ANCHOR_CONTEXT_CHARS)
    suffix_end = min(len(message_text), end + ANCHOR_CONTEXT_CHARS)
    return {
        "exact": quoted_text,
        "prefix": message_text[prefix_start:start] or None,
        "suffix": message_text[end:suffix_end] or None,
        "hint": start,
    }


def _find_all_exact_spans(message_text: str, quoted_text: str) -> list[tuple[int, int]]:
    if not message_text or not quoted_text:
        return []

    spans: list[tuple[int, int]] = []
    start = message_text.find(quoted_text)
    while start >= 0:
        spans.append((start, start + len(quoted_text)))
        start = message_text.find(quoted_text, start + 1)
    return spans


def _find_all_normalized_spans(message_text: str, quoted_text: str) -> list[tuple[int, int]]:
    normalized_message, raw_offsets = _normalize_text_with_offset_map(message_text, strip_markdown=True)
    normalized_quote, _ = _normalize_text_with_offset_map(quoted_text, strip_markdown=True)
    if not normalized_message or not normalized_quote:
        return []

    spans: list[tuple[int, int]] = []
    start = normalized_message.find(normalized_quote)
    while start >= 0:
        end = start + len(normalized_quote)
        raw_start = raw_offsets[start]
        raw_end = raw_offsets[end - 1] + 1
        spans.append((raw_start, raw_end))
        start = normalized_message.find(normalized_quote, start + 1)
    return spans


def _find_best_fuzzy_span(
    message_text: str,
    quoted_text: str,
    *,
    source_kind: Literal["message", "tool_arg", "tool_result"],
) -> tuple[float, tuple[int, int], float] | None:
    normalized_message, raw_offsets = _normalize_text_with_offset_map(message_text, strip_markdown=True)
    normalized_quote, _ = _normalize_text_with_offset_map(quoted_text, strip_markdown=True)
    if len(normalized_quote) < MIN_FUZZY_CITATION_CHARS or not normalized_message:
        return None

    result = fuzz.partial_ratio_alignment(normalized_quote, normalized_message)
    if result is None or result.dest_end <= result.dest_start:
        return None

    raw_start = raw_offsets[result.dest_start]
    raw_end = raw_offsets[result.dest_end - 1] + 1
    stabilized_span = _stabilize_fuzzy_span(
        message_text,
        quoted_text,
        (raw_start, raw_end),
        source_kind=source_kind,
        baseline_score=float(result.score),
        normalized_quote=normalized_quote,
    )
    if stabilized_span is None:
        return None
    runner_up_score = _find_second_best_fuzzy_score(normalized_message, normalized_quote, (result.dest_start, result.dest_end))
    return float(result.score), stabilized_span, runner_up_score


def _stabilize_fuzzy_span(
    message_text: str,
    quoted_text: str,
    span: tuple[int, int],
    *,
    source_kind: Literal["message", "tool_arg", "tool_result"],
    baseline_score: float,
    normalized_quote: str,
) -> tuple[int, int] | None:
    base_span = _trim_span_whitespace(message_text, span)
    if base_span is None:
        return None

    candidates: list[tuple[int, int]] = [base_span]
    word_span = _expand_span_to_word_boundaries(message_text, base_span)
    if word_span != base_span:
        candidates.append(word_span)

    if source_kind != "tool_arg" and _quote_looks_sentence_like(quoted_text):
        sentence_span = _expand_span_to_sentence_boundaries(message_text, word_span)
        if sentence_span != word_span:
            candidates.append(sentence_span)

    best_span: tuple[int, int] | None = None
    best_key: tuple[int, float, int] | None = None
    seen: set[tuple[int, int]] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if _span_extension_chars(base_span, candidate) > MAX_FUZZY_BOUNDARY_EXTENSION_CHARS:
            continue
        candidate_text = message_text[candidate[0]:candidate[1]]
        normalized_candidate, _ = _normalize_text_with_offset_map(candidate_text, strip_markdown=True)
        if not normalized_candidate:
            continue
        candidate_score = float(fuzz.partial_ratio(normalized_quote, normalized_candidate))
        if baseline_score - candidate_score > FUZZY_BOUNDARY_SCORE_TOLERANCE:
            continue
        key = (
            _span_boundary_quality(message_text, candidate),
            candidate_score,
            -(candidate[1] - candidate[0]),
        )
        if best_key is None or key > best_key:
            best_span = candidate
            best_key = key

    if best_span is None:
        return None
    if not _is_span_start_boundary(message_text, best_span[0]):
        return None
    if not _is_span_end_boundary(message_text, best_span[1]):
        return None
    return best_span


def _trim_span_whitespace(message_text: str, span: tuple[int, int]) -> tuple[int, int] | None:
    start, end = span
    while start < end and message_text[start].isspace():
        start += 1
    while end > start and message_text[end - 1].isspace():
        end -= 1
    if end <= start:
        return None
    return start, end


def _expand_span_to_word_boundaries(message_text: str, span: tuple[int, int]) -> tuple[int, int]:
    start, end = span
    while start > 0 and start < len(message_text) and message_text[start - 1].isalnum() and message_text[start].isalnum():
        start -= 1
    while end > 0 and end < len(message_text) and message_text[end - 1].isalnum() and message_text[end].isalnum():
        end += 1
    trimmed = _trim_span_whitespace(message_text, (start, end))
    return trimmed if trimmed is not None else span


def _expand_span_to_sentence_boundaries(message_text: str, span: tuple[int, int]) -> tuple[int, int]:
    start, end = span

    sentence_start = start
    while sentence_start > 0 and message_text[sentence_start - 1].isspace():
        sentence_start -= 1
    while sentence_start > 0 and message_text[sentence_start - 1] not in ".!?\n":
        sentence_start -= 1
    while sentence_start < len(message_text) and message_text[sentence_start].isspace():
        sentence_start += 1

    sentence_end = end
    while sentence_end < len(message_text) and message_text[sentence_end].isspace():
        sentence_end += 1
    while sentence_end < len(message_text) and message_text[sentence_end] not in ".!?\n":
        sentence_end += 1
    if sentence_end < len(message_text) and message_text[sentence_end] in ".!?":
        sentence_end += 1
        while sentence_end < len(message_text) and message_text[sentence_end] in "\"')]}":
            sentence_end += 1

    trimmed = _trim_span_whitespace(message_text, (sentence_start, sentence_end))
    return trimmed if trimmed is not None else span


def _quote_looks_sentence_like(quoted_text: str) -> bool:
    stripped = quoted_text.strip()
    if not stripped:
        return False
    words = re.findall(r"\w+", stripped)
    if len(words) < 6:
        return False
    return stripped[:1].isupper() or stripped[-1:] in ".!?"


def _span_extension_chars(base_span: tuple[int, int], candidate_span: tuple[int, int]) -> int:
    return max(0, base_span[0] - candidate_span[0]) + max(0, candidate_span[1] - base_span[1])


def _span_boundary_quality(message_text: str, span: tuple[int, int]) -> int:
    start, end = span
    quality = 0
    if _is_span_start_boundary(message_text, start):
        quality += 1
    if _is_span_end_boundary(message_text, end):
        quality += 1
    if _is_sentence_start_boundary(message_text, start):
        quality += 1
    if _is_sentence_end_boundary(message_text, end):
        quality += 1
    return quality


def _is_span_start_boundary(message_text: str, start: int) -> bool:
    if start <= 0 or start >= len(message_text):
        return True
    return not (message_text[start - 1].isalnum() and message_text[start].isalnum())


def _is_span_end_boundary(message_text: str, end: int) -> bool:
    if end <= 0 or end >= len(message_text):
        return True
    return not (message_text[end - 1].isalnum() and message_text[end].isalnum())


def _is_sentence_start_boundary(message_text: str, start: int) -> bool:
    if start <= 0:
        return True
    probe = start
    while probe > 0 and message_text[probe - 1].isspace():
        probe -= 1
    if probe <= 0:
        return True
    return message_text[probe - 1] in ".!?\n"


def _is_sentence_end_boundary(message_text: str, end: int) -> bool:
    if end >= len(message_text):
        return True
    probe = end
    while probe < len(message_text) and message_text[probe].isspace():
        probe += 1
    if probe >= len(message_text):
        return True
    return message_text[probe] in ".!?\n"


def _find_second_best_fuzzy_score(
    normalized_message: str,
    normalized_quote: str,
    best_span: tuple[int, int],
) -> float:
    if not normalized_message or not normalized_quote:
        return 0.0

    start, end = best_span
    if end <= start:
        return 0.0
    masked_chars = list(normalized_message)
    for index in range(start, min(end, len(masked_chars))):
        masked_chars[index] = " "
    runner_up = fuzz.partial_ratio_alignment(normalized_quote, "".join(masked_chars))
    if runner_up is None:
        return 0.0
    return float(runner_up.score)


def _normalize_text_with_offset_map(
    raw_text: str,
    *,
    strip_markdown: bool,
) -> tuple[str, list[int]]:
    normalized_chars: list[str] = []
    raw_offsets: list[int] = []
    pending_space_index: int | None = None

    for index, raw_char in enumerate(raw_text):
        if strip_markdown and _is_markdown_formatting_marker(raw_text, index):
            continue
        char = NORMALIZED_QUOTE_CHARS.get(raw_char, raw_char)
        if char.isspace():
            if normalized_chars:
                pending_space_index = index if pending_space_index is None else pending_space_index
            continue
        if pending_space_index is not None:
            normalized_chars.append(" ")
            raw_offsets.append(pending_space_index)
            pending_space_index = None
        normalized_chars.append(char)
        raw_offsets.append(index)
    return "".join(normalized_chars), raw_offsets


def _is_markdown_formatting_marker(text: str, index: int) -> bool:
    char = text[index]
    if char == "`":
        return True
    if char not in {"*", "_"}:
        return False

    prev_char = text[index - 1] if index > 0 else ""
    next_char = text[index + 1] if index + 1 < len(text) else ""
    if prev_char == char or next_char == char:
        return True
    return _is_word_boundary(prev_char) != _is_word_boundary(next_char)


def _is_word_boundary(char: str) -> bool:
    return not char or not char.isalnum()
