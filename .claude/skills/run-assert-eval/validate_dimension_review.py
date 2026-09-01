#!/usr/bin/env python3
"""Render and validate ASSERT harm dimension-review ledgers."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml


NAMESPACES = ("behavior_categories", "test_dimensions", "judge_dimensions")
NAMESPACE_HEADINGS = {
    "behavior_categories": "Behavior Categories",
    "test_dimensions": "Test-Set Dimensions",
    "judge_dimensions": "Judge Dimensions",
}
CITATION_TAG = re.compile(r"^\[[1-9][0-9]*\]$")
CYCLE_STATUSES = {"pending_review", "superseded", "approved"}
CANDIDATE_DISPOSITIONS = {"keep", "merge", "reject"}
EVALUATION_PURPOSES = {
    "model_comparison",
    "product_readiness",
    "mitigation_validation",
    "regression_testing",
    "red_team_discovery",
}
EVALUATION_INTENT_FIELDS = {"decision", "purposes", "population"}


def _builtin_judge_dimension_names() -> frozenset[str]:
    """Names ASSERT always judges, which a config dimension would silently replace.

    Read from ``assert_ai`` when importable so the gate cannot drift from the
    runtime; the literal fallback keeps this script standalone.
    """
    try:
        from assert_ai.core.judge import BUILT_IN_DIMENSIONS
    except Exception:
        return frozenset({"policy_violation", "overrefusal"})
    names = {
        dimension["name"]
        for dimension in BUILT_IN_DIMENSIONS
        if isinstance(dimension, dict) and isinstance(dimension.get("name"), str)
    }
    return frozenset(names) or frozenset({"policy_violation", "overrefusal"})


BUILT_IN_JUDGE_DIMENSIONS = _builtin_judge_dimension_names()


class ReviewValidationError(ValueError):
    """Raised when a review ledger violates its deterministic contract."""


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReviewValidationError(f"{label} must be a mapping")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ReviewValidationError(f"{label} must be a list")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReviewValidationError(f"{label} must be a non-empty string")
    text = value.strip()
    if text.startswith("<") and text.endswith(">"):
        raise ReviewValidationError(f"{label} still contains a placeholder")
    return text


def _optional_text(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return _text(value, label)


def _string_list(value: Any, label: str, *, minimum: int = 0) -> list[str]:
    items = _list(value, label)
    if len(items) < minimum:
        raise ReviewValidationError(f"{label} must contain at least {minimum} item(s)")
    return [_text(item, f"{label}[{index}]") for index, item in enumerate(items)]


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ReviewValidationError(f"{label} must be a positive integer")
    return value


def _citation_tags(
    value: Any,
    label: str,
    references: dict[str, Any],
    *,
    minimum: int = 0,
) -> list[str]:
    tags = _string_list(value, label, minimum=minimum)
    if len(tags) != len(set(tags)):
        raise ReviewValidationError(f"{label} contains duplicate citation tags")
    for tag in tags:
        if not CITATION_TAG.fullmatch(tag):
            raise ReviewValidationError(f"{label} contains invalid citation tag {tag!r}")
        if tag not in references:
            raise ReviewValidationError(f"{label} references undefined citation {tag}")
    return tags


def _split_review(path: Path) -> tuple[dict[str, Any], str, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ReviewValidationError(f"{path} must start with YAML frontmatter")
    boundary = text.find("\n---\n", 4)
    if boundary < 0:
        raise ReviewValidationError(f"{path} has no closing frontmatter delimiter")
    frontmatter = text[4:boundary]
    data = yaml.safe_load(frontmatter)
    if not isinstance(data, dict):
        raise ReviewValidationError(f"{path} frontmatter must contain a mapping")
    prefix = text[: boundary + 5]
    body = text[boundary + 5 :]
    return data, prefix, body


def _validate_references(data: dict[str, Any]) -> dict[str, Any]:
    references = _mapping(data.get("references"), "references")
    if not references:
        raise ReviewValidationError("references must not be empty")
    for tag, raw_reference in references.items():
        if not isinstance(tag, str) or not CITATION_TAG.fullmatch(tag):
            raise ReviewValidationError(f"references contains invalid tag {tag!r}")
        reference = _mapping(raw_reference, f"references.{tag}")
        _text(reference.get("title"), f"references.{tag}.title")
        _text(reference.get("url"), f"references.{tag}.url")
        accessed = _text(reference.get("accessed"), f"references.{tag}.accessed")
        try:
            date.fromisoformat(accessed)
        except ValueError as error:
            raise ReviewValidationError(
                f"references.{tag}.accessed must use YYYY-MM-DD"
            ) from error
    return references


def _validate_evaluation_intent(
    data: dict[str, Any],
) -> tuple[dict[str, Any], set[str]]:
    raw_intent = data.get("evaluation_intent")
    if raw_intent is None:
        return {"decision": None, "purposes": [], "population": None}, set()

    intent = _mapping(raw_intent, "evaluation_intent")
    decision = _optional_text(intent.get("decision"), "evaluation_intent.decision")
    purposes = _string_list(
        intent.get("purposes", []), "evaluation_intent.purposes"
    )
    if len(purposes) != len(set(purposes)):
        raise ReviewValidationError("evaluation_intent.purposes contains duplicates")
    unknown_purposes = set(purposes) - EVALUATION_PURPOSES
    if unknown_purposes:
        raise ReviewValidationError(
            "evaluation_intent.purposes contains unsupported values: "
            f"{sorted(unknown_purposes)}"
        )
    population = _optional_text(
        intent.get("population"), "evaluation_intent.population"
    )
    normalized = {
        "decision": decision,
        "purposes": purposes,
        "population": population,
    }
    answered_fields = {
        field
        for field, value in normalized.items()
        if value not in (None, [], "")
    }
    return normalized, answered_fields


def _validate_cycle(
    cycle: dict[str, Any],
    *,
    n: int,
    references: dict[str, Any],
    intent_fields: set[str],
    label: str,
) -> None:
    _text(cycle.get("id"), f"{label}.id")
    _text(cycle.get("criteria_version"), f"{label}.criteria_version")
    _string_list(cycle.get("criteria"), f"{label}.criteria", minimum=1)
    status = _text(cycle.get("status"), f"{label}.status")
    if status not in CYCLE_STATUSES:
        raise ReviewValidationError(
            f"{label}.status must be one of {sorted(CYCLE_STATUSES)}"
        )

    passes = _list(cycle.get("passes"), f"{label}.passes")
    if len(passes) != n:
        raise ReviewValidationError(f"{label}.passes must contain exactly n={n} passes")

    candidate_by_id: dict[str, tuple[str, int, str, set[str]]] = {}
    pass_numbers: list[int] = []
    for pass_index, raw_pass in enumerate(passes):
        pass_label = f"{label}.passes[{pass_index}]"
        generation_pass = _mapping(raw_pass, pass_label)
        number = _positive_int(generation_pass.get("number"), f"{pass_label}.number")
        pass_numbers.append(number)
        if generation_pass.get("complete") is not True:
            raise ReviewValidationError(f"{pass_label}.complete must be true")
        applied_intent = set(
            _string_list(
                generation_pass.get("intent_fields_applied", []),
                f"{pass_label}.intent_fields_applied",
            )
        )
        unsupported_intent = applied_intent - EVALUATION_INTENT_FIELDS
        if unsupported_intent:
            raise ReviewValidationError(
                f"{pass_label}.intent_fields_applied contains unsupported fields: "
                f"{sorted(unsupported_intent)}"
            )
        if applied_intent != intent_fields:
            raise ReviewValidationError(
                f"{pass_label}.intent_fields_applied must match answered evaluation intent "
                f"fields {sorted(intent_fields)}"
            )
        _string_list(
            generation_pass.get("search_branches"),
            f"{pass_label}.search_branches",
            minimum=1,
        )
        if generation_pass.get("breadth_audit_complete") is not True:
            raise ReviewValidationError(f"{pass_label}.breadth_audit_complete must be true")
        no_new_passes = _positive_int(
            generation_pass.get("no_new_dimension_passes"),
            f"{pass_label}.no_new_dimension_passes",
        )
        if no_new_passes < 2:
            raise ReviewValidationError(
                f"{pass_label}.no_new_dimension_passes must be at least 2"
            )

        candidates = _mapping(generation_pass.get("candidates"), f"{pass_label}.candidates")
        for namespace in NAMESPACES:
            namespace_candidates = _list(
                candidates.get(namespace), f"{pass_label}.candidates.{namespace}"
            )
            for candidate_index, raw_candidate in enumerate(namespace_candidates):
                candidate_label = (
                    f"{pass_label}.candidates.{namespace}[{candidate_index}]"
                )
                candidate = _mapping(raw_candidate, candidate_label)
                candidate_id = _text(candidate.get("id"), f"{candidate_label}.id")
                if candidate_id in candidate_by_id:
                    raise ReviewValidationError(
                        f"{label} contains duplicate candidate id {candidate_id!r}"
                    )
                _text(candidate.get("name"), f"{candidate_label}.name")
                disposition = _text(
                    candidate.get("disposition"), f"{candidate_label}.disposition"
                )
                if disposition not in CANDIDATE_DISPOSITIONS:
                    raise ReviewValidationError(
                        f"{candidate_label}.disposition must be one of "
                        f"{sorted(CANDIDATE_DISPOSITIONS)}"
                    )
                minimum = 0 if disposition == "reject" else 1
                tags = _citation_tags(
                    candidate.get("citation_tags"),
                    f"{candidate_label}.citation_tags",
                    references,
                    minimum=minimum,
                )
                candidate_by_id[candidate_id] = (
                    namespace,
                    number,
                    disposition,
                    set(tags),
                )

    if sorted(pass_numbers) != list(range(1, n + 1)):
        raise ReviewValidationError(f"{label}.passes numbers must be exactly 1 through {n}")

    deduplication = _mapping(cycle.get("deduplication"), f"{label}.deduplication")
    if deduplication.get("completed") is not True:
        raise ReviewValidationError(f"{label}.deduplication.completed must be true")
    if deduplication.get("duplicate_audit_complete") is not True:
        raise ReviewValidationError(
            f"{label}.deduplication.duplicate_audit_complete must be true"
        )
    namespaces = _mapping(
        deduplication.get("namespaces"), f"{label}.deduplication.namespaces"
    )

    accounted: set[str] = set()
    canonical_ids: set[str] = set()
    canonical_counts: dict[str, int] = {}
    for namespace in NAMESPACES:
        canonical_items = _list(
            namespaces.get(namespace),
            f"{label}.deduplication.namespaces.{namespace}",
        )
        canonical_counts[namespace] = len(canonical_items)
        for item_index, raw_item in enumerate(canonical_items):
            item_label = f"{label}.deduplication.namespaces.{namespace}[{item_index}]"
            item = _mapping(raw_item, item_label)
            canonical_id = _text(item.get("id"), f"{item_label}.id")
            if canonical_id in canonical_ids:
                raise ReviewValidationError(
                    f"{label} contains duplicate canonical id {canonical_id!r}"
                )
            canonical_ids.add(canonical_id)
            _text(item.get("name"), f"{item_label}.name")
            if namespace == "judge_dimensions":
                canonical_name = str(item["name"]).strip()
                if canonical_name in BUILT_IN_JUDGE_DIMENSIONS:
                    raise ReviewValidationError(
                        f"{item_label}.name {canonical_name!r} reuses a built-in judge "
                        "dimension. Config dimensions merge over the built-ins by name, so "
                        "this silently replaces the built-in rubric: the verdict stored in "
                        "the run JSON and the default compare metric would no longer mean "
                        "what the engine documents. Rename it to a distinct researched name."
                    )
            _text(item.get("purpose"), f"{item_label}.purpose")
            _text(item.get("levels_or_mode"), f"{item_label}.levels_or_mode")
            _text(item.get("observability"), f"{item_label}.observability")
            if item.get("executable") is not True:
                raise ReviewValidationError(f"{item_label}.executable must be true")
            _string_list(item.get("aliases"), f"{item_label}.aliases")
            source_items = _string_list(
                item.get("source_items"), f"{item_label}.source_items", minimum=1
            )
            if len(source_items) != len(set(source_items)):
                raise ReviewValidationError(f"{item_label}.source_items contains duplicates")
            source_passes = _list(item.get("source_passes"), f"{item_label}.source_passes")
            if any(isinstance(value, bool) or not isinstance(value, int) for value in source_passes):
                raise ReviewValidationError(f"{item_label}.source_passes must contain integers")
            minimum_citations = 1 if namespace == "behavior_categories" else 2
            canonical_tags = set(
                _citation_tags(
                    item.get("citation_tags"),
                    f"{item_label}.citation_tags",
                    references,
                    minimum=minimum_citations,
                )
            )
            _text(item.get("rationale"), f"{item_label}.rationale")
            if intent_fields:
                _text(item.get("intent_alignment"), f"{item_label}.intent_alignment")
            elif item.get("intent_alignment") is not None:
                _text(item.get("intent_alignment"), f"{item_label}.intent_alignment")

            derived_passes: set[int] = set()
            source_tags: set[str] = set()
            for source_item in source_items:
                if source_item not in candidate_by_id:
                    raise ReviewValidationError(
                        f"{item_label}.source_items references unknown candidate {source_item!r}"
                    )
                source_namespace, source_pass, disposition, tags = candidate_by_id[source_item]
                if source_namespace != namespace:
                    raise ReviewValidationError(
                        f"{item_label} crosses namespace boundary via {source_item!r}"
                    )
                if disposition == "reject":
                    raise ReviewValidationError(
                        f"{item_label} retains rejected candidate {source_item!r}"
                    )
                if source_item in accounted:
                    raise ReviewValidationError(
                        f"candidate {source_item!r} is accounted for more than once"
                    )
                accounted.add(source_item)
                derived_passes.add(source_pass)
                source_tags.update(tags)
            if sorted(source_passes) != sorted(derived_passes):
                raise ReviewValidationError(
                    f"{item_label}.source_passes does not match its source candidates"
                )
            if not canonical_tags.issubset(source_tags):
                raise ReviewValidationError(
                    f"{item_label}.citation_tags contains evidence absent from its source candidates"
                )

    rejections = _list(
        deduplication.get("rejections"), f"{label}.deduplication.rejections"
    )
    for rejection_index, raw_rejection in enumerate(rejections):
        rejection_label = f"{label}.deduplication.rejections[{rejection_index}]"
        rejection = _mapping(raw_rejection, rejection_label)
        namespace = _text(rejection.get("namespace"), f"{rejection_label}.namespace")
        if namespace not in NAMESPACES:
            raise ReviewValidationError(
                f"{rejection_label}.namespace must be one of {list(NAMESPACES)}"
            )
        source_items = _string_list(
            rejection.get("source_items"), f"{rejection_label}.source_items", minimum=1
        )
        _text(rejection.get("rationale"), f"{rejection_label}.rationale")
        for source_item in source_items:
            if source_item not in candidate_by_id:
                raise ReviewValidationError(
                    f"{rejection_label}.source_items references unknown candidate {source_item!r}"
                )
            source_namespace, _, disposition, _ = candidate_by_id[source_item]
            if source_namespace != namespace or disposition != "reject":
                raise ReviewValidationError(
                    f"{rejection_label} may account only for rejected {namespace} candidates"
                )
            if source_item in accounted:
                raise ReviewValidationError(
                    f"candidate {source_item!r} is accounted for more than once"
                )
            accounted.add(source_item)

    missing = set(candidate_by_id) - accounted
    if missing:
        raise ReviewValidationError(
            f"{label}.deduplication does not account for candidates: {sorted(missing)}"
        )
    for namespace in ("behavior_categories", "test_dimensions"):
        if canonical_counts[namespace] == 0:
            raise ReviewValidationError(
                f"{label}.deduplication.namespaces.{namespace} must retain at least one item"
            )


def _validate_approval(
    data: dict[str, Any],
    *,
    active_cycle: dict[str, Any],
    require_approval: bool,
) -> None:
    approval = _mapping(data.get("approval"), "approval")
    status = _text(approval.get("status"), "approval.status")
    if status not in {"pending", "approved"}:
        raise ReviewValidationError("approval.status must be pending or approved")
    cycle_id = _text(approval.get("cycle_id"), "approval.cycle_id")
    if cycle_id != active_cycle["id"]:
        raise ReviewValidationError("approval.cycle_id must match active_cycle")
    criteria_version = _text(approval.get("criteria_version"), "approval.criteria_version")
    if criteria_version != active_cycle["criteria_version"]:
        raise ReviewValidationError(
            "approval.criteria_version must match the active cycle criteria_version"
        )
    active_status = active_cycle["status"]
    if (status == "approved") != (active_status == "approved"):
        raise ReviewValidationError(
            "approval.status and active cycle status must become approved together"
        )
    if not require_approval and status == "pending":
        return
    if status != "approved":
        raise ReviewValidationError("explicit user approval is required before writing YAML")
    if approval.get("relevance") != "approved":
        raise ReviewValidationError("approval.relevance must be approved")
    _text(approval.get("edits"), "approval.edits")
    _text(approval.get("response"), "approval.response")
    if approval.get("approved_by") != "user":
        raise ReviewValidationError("approval.approved_by must be user")
    approved_at = _text(approval.get("approved_at"), "approval.approved_at")
    try:
        parsed = datetime.fromisoformat(approved_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise ReviewValidationError("approval.approved_at must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise ReviewValidationError("approval.approved_at must include a timezone")
    if parsed > datetime.now(timezone.utc):
        raise ReviewValidationError("approval.approved_at cannot be in the future")


def validate_review(data: dict[str, Any], *, require_approval: bool = False) -> None:
    if data.get("schema_version") != 1:
        raise ReviewValidationError("schema_version must be 1")
    _text(data.get("harm_name"), "harm_name")
    n = _positive_int(data.get("n"), "n")
    active_cycle_id = _text(data.get("active_cycle"), "active_cycle")
    _, intent_fields = _validate_evaluation_intent(data)
    references = _validate_references(data)
    cycles = _list(data.get("cycles"), "cycles")
    if not cycles:
        raise ReviewValidationError("cycles must not be empty")

    cycle_by_id: dict[str, dict[str, Any]] = {}
    for cycle_index, raw_cycle in enumerate(cycles):
        label = f"cycles[{cycle_index}]"
        cycle = _mapping(raw_cycle, label)
        _validate_cycle(
            cycle,
            n=n,
            references=references,
            intent_fields=intent_fields,
            label=label,
        )
        cycle_id = cycle["id"]
        if cycle_id in cycle_by_id:
            raise ReviewValidationError(f"cycles contains duplicate id {cycle_id!r}")
        cycle_by_id[cycle_id] = cycle

    if active_cycle_id not in cycle_by_id:
        raise ReviewValidationError("active_cycle does not identify a cycle")
    if cycles[-1]["id"] != active_cycle_id:
        raise ReviewValidationError("active_cycle must be the final cycle")
    for cycle in cycles[:-1]:
        if cycle["status"] != "superseded":
            raise ReviewValidationError("every cycle before active_cycle must be superseded")
    active_cycle = cycle_by_id[active_cycle_id]
    if active_cycle["status"] not in {"pending_review", "approved"}:
        raise ReviewValidationError("active_cycle must be pending_review or approved")
    _validate_approval(data, active_cycle=active_cycle, require_approval=require_approval)


def _cell(value: Any) -> str:
    if isinstance(value, list):
        value = "<br>".join(str(item) for item in value) or "none"
    elif value in (None, ""):
        value = "none"
    return str(value).replace("\n", "<br>").replace("|", "\\|")


def _intent_cell(value: Any) -> str:
    if value in (None, "", []):
        return "not provided; default workflow used"
    return _cell(value)


def render_review_body(data: dict[str, Any]) -> str:
    cycle = next(item for item in data["cycles"] if item["id"] == data["active_cycle"])
    deduplication = cycle["deduplication"]
    evaluation_intent, _ = _validate_evaluation_intent(data)
    lines = [
        "<!-- Generated from YAML frontmatter by validate_dimension_review.py. -->",
        f"# Dimension Review: {_cell(data['harm_name'])}",
        "",
        "## Evaluation Intent",
        "",
        "| Field | Answer |",
        "|---|---|",
        f"| Decision supported | {_intent_cell(evaluation_intent['decision'])} |",
        f"| Purpose(s) | {_intent_cell(evaluation_intent['purposes'])} |",
        f"| System users/affected groups | {_intent_cell(evaluation_intent['population'])} |",
        "",
        f"**Active cycle:** `{_cell(cycle['id'])}`  ",
        f"**Criteria version:** `{_cell(cycle['criteria_version'])}`  ",
        f"**Criteria:** {_cell(cycle['criteria'])}  ",
        f"**Generation passes:** `{data['n']}`  ",
        f"**Review status:** `{_cell(cycle['status'])}`",
        "",
    ]

    for namespace in NAMESPACES:
        lines.extend(
            [
                f"## {NAMESPACE_HEADINGS[namespace]}",
                "",
                "| Name | Purpose | Intent alignment | Levels or mode | Observability | Executable | Sources | Passes |",
                "|---|---|---|---|---|---|---|---|",
            ]
        )
        items = deduplication["namespaces"][namespace]
        if items:
            for item in items:
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            _cell(item["name"]),
                            _cell(item["purpose"]),
                            _cell(item.get("intent_alignment")),
                            _cell(item["levels_or_mode"]),
                            _cell(item["observability"]),
                            "yes" if item["executable"] else "no",
                            _cell(item["citation_tags"]),
                            _cell(item["source_passes"]),
                        ]
                    )
                    + " |"
                )
        else:
            lines.append("| _None retained_ |  |  |  |  |  |  |  |")
        lines.append("")

    candidate_names = {
        candidate["id"]: candidate["name"]
        for generation_pass in cycle["passes"]
        for namespace in NAMESPACES
        for candidate in generation_pass["candidates"][namespace]
    }
    lines.extend(
        [
            "## Merge And Rejection Decisions",
            "",
            "| Decision | Canonical item | Source candidates | Rationale |",
            "|---|---|---|---|",
        ]
    )
    decision_count = 0
    for namespace in NAMESPACES:
        for item in deduplication["namespaces"][namespace]:
            if len(item["source_items"]) > 1 or item["aliases"]:
                decision_count += 1
                lines.append(
                    f"| merge | {_cell(item['name'])} | {_cell(item['source_items'])} | "
                    f"{_cell(item['rationale'])} |"
                )
    for rejection in deduplication["rejections"]:
        decision_count += 1
        names = [candidate_names[source] for source in rejection["source_items"]]
        lines.append(
            f"| reject | {_cell(names)} | {_cell(rejection['source_items'])} | "
            f"{_cell(rejection['rationale'])} |"
        )
    if decision_count == 0:
        lines.append("| none |  |  | No merges or rejections. |")
    lines.append("")

    lines.extend(
        [
            "## Cycle History",
            "",
            "| Cycle | Criteria version | Status | Criteria |",
            "|---|---|---|---|",
        ]
    )
    for history_cycle in data["cycles"]:
        lines.append(
            f"| {_cell(history_cycle['id'])} | {_cell(history_cycle['criteria_version'])} | "
            f"{_cell(history_cycle['status'])} | {_cell(history_cycle['criteria'])} |"
        )
    lines.append("")

    approval = data["approval"]
    lines.extend(
        [
            "## Approval",
            "",
            "1. Are these dimensions relevant to this harm and target: approve, revise, or regenerate?",
            "2. What specific edits or additional generation criteria should be applied?",
            "",
            "| Field | Value |",
            "|---|---|",
            f"| Status | {_cell(approval['status'])} |",
            f"| Relevance | {_cell(approval['relevance'])} |",
            f"| Requested edits | {_cell(approval['edits'])} |",
            f"| User response | {_cell(approval['response'])} |",
            f"| Approved by | {_cell(approval['approved_by'])} |",
            f"| Approved at | {_cell(approval['approved_at'])} |",
            "",
        ]
    )
    return "\n".join(lines)


def _validate_review_file(path: Path, *, require_approval: bool) -> dict[str, Any]:
    data, _, body = _split_review(path)
    validate_review(data, require_approval=require_approval)
    expected_body = render_review_body(data)
    if body != expected_body:
        raise ReviewValidationError(
            f"{path} body is stale; run the render command after editing frontmatter"
        )
    return data


def render_review(path: Path) -> None:
    data, prefix, _ = _split_review(path)
    validate_review(data)
    path.write_text(prefix + render_review_body(data), encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _default_stamp_path(review_path: Path) -> Path:
    return review_path.with_suffix(".approval-stamp.json")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def pre_write(review_path: Path, config_path: Path, stamp_path: Path) -> None:
    _validate_review_file(review_path, require_approval=True)
    if config_path.exists():
        raise ReviewValidationError(
            f"config path already exists; choose a new isolated generation directory: {config_path}"
        )
    stamp = {
        "schema_version": 1,
        "review_path": str(review_path.resolve()),
        "review_sha256": _sha256(review_path),
        "config_path": str(config_path.resolve()),
        "pre_write_validated_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_json(stamp_path, stamp)


def _dimension_names(dimensions: object) -> list[str]:
    """Extract dimension names from either the mapping or the list YAML form."""

    if isinstance(dimensions, dict):
        return [str(key).strip() for key in dimensions]
    if isinstance(dimensions, list):
        return [
            str(item.get("name", "")).strip()
            for item in dimensions
            if isinstance(item, dict)
        ]
    return []


def _preset_dimension_names(judge: dict) -> dict[str, list[str]]:
    """Resolve `pipeline.judge.preset` to the dimension names it contributes.

    Presets expand into the same merged dimension list as inline `dimensions`
    (assert_ai/config.py), so a preset can shadow a built-in exactly as an inline
    dimension can. A named preset that cannot be resolved or parsed raises: this
    check exists to prove no built-in is shadowed, and skipping an unreadable
    preset would report "checked and clean" for something never read.
    """

    raw = judge.get("preset")
    if isinstance(raw, str):
        preset_names = [raw.strip()]
    elif isinstance(raw, list):
        preset_names = [str(item).strip() for item in raw if item]
    else:
        return {}

    resolved: dict[str, list[str]] = {}
    for preset_name in preset_names:
        if not preset_name:
            continue
        text = _read_judge_preset_text(preset_name)
        if text is None:
            raise ReviewValidationError(
                f"judge preset {preset_name!r} is named by the config but could not "
                "be resolved, so its dimensions cannot be checked for shadowing of "
                f"the built-ins ({', '.join(sorted(BUILT_IN_JUDGE_DIMENSIONS))}). "
                "Install ASSERT into this environment (`pip install -e .`) or run "
                "this validator from a source checkout, then re-run. Passing "
                "without reading the preset would report a check that never "
                "happened."
            )
        try:
            preset = yaml.safe_load(text)
        except yaml.YAMLError as error:
            raise ReviewValidationError(
                f"judge preset {preset_name!r} is not valid YAML, so its dimensions "
                "cannot be checked for shadowing of the built-ins."
            ) from error
        if not isinstance(preset, dict):
            raise ReviewValidationError(
                f"judge preset {preset_name!r} does not contain a top-level mapping, "
                "so its dimensions cannot be checked for shadowing of the built-ins."
            )
        names = [name for name in _dimension_names(preset.get("dimensions")) if name]
        if names:
            resolved[preset_name] = names
    return resolved


def _read_judge_preset_text(preset_name: str) -> str | None:
    """Return the YAML text of a judge preset, or ``None`` if it cannot be found.

    Resolution starts with the installed package. ``assert_ai.library.judges``
    ships in the wheel (`pyproject.toml` package-data), so `importlib.resources`
    finds it wherever ASSERT is installed. The filesystem walk that follows only
    ever succeeds inside a source checkout, which is why it cannot be the sole
    strategy: under a wheel install every preset would resolve to ``None`` and
    the anti-shadowing check would pass without reading anything.
    """

    if "/" in preset_name or "\\" in preset_name or preset_name.startswith("."):
        return None

    try:
        from importlib.resources import files as _resource_files

        resource = _resource_files("assert_ai.library.judges") / f"{preset_name}.yaml"
        if resource.is_file():
            return resource.read_text(encoding="utf-8")
    except (ImportError, ModuleNotFoundError, FileNotFoundError, OSError, TypeError):
        pass

    for base in (Path(__file__).resolve(), Path.cwd().resolve() / "_"):
        for parent in base.parents:
            candidate = (
                parent / "assert_ai" / "library" / "judges" / f"{preset_name}.yaml"
            )
            if candidate.is_file():
                try:
                    return candidate.read_text(encoding="utf-8")
                except OSError:
                    return None
    return None


def _reject_shadowing_judge_dimensions(config: dict, config_path: Path) -> None:
    """Reject a written config whose judge dimensions shadow a built-in name.

    The canonical-item check guards the review ledger, but the config is authored
    separately. Without this the artifact that actually reaches the judge is
    unchecked. Covers both inline `dimensions` and `preset`-contributed ones,
    since both merge over the built-ins by name.
    """

    pipeline = config.get("pipeline")
    if not isinstance(pipeline, dict):
        return
    judge = pipeline.get("judge")
    if not isinstance(judge, dict):
        return

    problems: list[str] = []

    inline = sorted(
        {
            name
            for name in _dimension_names(judge.get("dimensions"))
            if name in BUILT_IN_JUDGE_DIMENSIONS
        }
    )
    if inline:
        problems.append(
            "declares judge dimension(s) "
            f"{', '.join(repr(name) for name in inline)} that reuse a built-in name"
        )

    for preset_name, names in sorted(_preset_dimension_names(judge).items()):
        shadowed = sorted({n for n in names if n in BUILT_IN_JUDGE_DIMENSIONS})
        if shadowed:
            problems.append(
                f"selects judge preset {preset_name!r}, which defines "
                f"{', '.join(repr(name) for name in shadowed)} - reusing a built-in name"
            )

    if problems:
        raise ReviewValidationError(
            f"config {config_path} "
            + "; ".join(problems)
            + ". Judge dimensions from both `dimensions` and `preset` merge over the "
            "built-ins by name, so this silently replaces the built-in rubric: the verdict "
            "stored in the run JSON and the default compare metric would no longer mean "
            "what the engine documents. Rename to a distinct researched name, or drop the "
            "preset - the built-ins already provide these dimensions, and the engine treats "
            "them as superseded once the permissibility split is available."
        )


def _normalize_item_name(name: str) -> str:
    """Fold a dimension name to a comparison key.

    The review ledger and the config are written by different steps, so a name
    may legitimately differ in case or separator (``Task Framing`` vs
    ``task_framing``). Folding those keeps the gate from failing on cosmetics
    while still catching a config whose dimension set is genuinely not the one
    that was approved.
    """

    return re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower()).strip("_")


def _config_dimension_names(config: dict, *, judge: bool) -> list[str]:
    """Pull judge or test-set dimension names out of a written config."""

    pipeline = config.get("pipeline")
    if not isinstance(pipeline, dict):
        return []
    if judge:
        section = pipeline.get("judge")
        if not isinstance(section, dict):
            return []
        return _dimension_names(section.get("dimensions"))
    test_set = pipeline.get("test_set")
    if not isinstance(test_set, dict):
        return []
    stratify = test_set.get("stratify")
    if not isinstance(stratify, dict):
        return []
    return _dimension_names(stratify.get("dimensions"))


def _approved_canonical_names(review: dict[str, Any], namespace: str) -> list[str]:
    """Canonical item names the active cycle approved for one namespace."""

    cycles = review.get("cycles")
    if not isinstance(cycles, list) or not cycles:
        return []
    active_id = review.get("active_cycle")
    cycle = next(
        (
            item
            for item in cycles
            if isinstance(item, dict) and item.get("id") == active_id
        ),
        None,
    )
    if cycle is None:
        return []
    deduplication = cycle.get("deduplication")
    if not isinstance(deduplication, dict):
        return []
    namespaces = deduplication.get("namespaces")
    if not isinstance(namespaces, dict):
        return []
    items = namespaces.get(namespace)
    if not isinstance(items, list):
        return []
    return [
        str(item["name"]).strip()
        for item in items
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    ]


def _validate_runtime_schema(config_path: Path) -> None:
    """Load the config through the engine's own path, as ``assert-ai run`` does.

    Parsing as YAML proves only that the file is YAML. A config can be a valid
    mapping and still be rejected by the runtime for an unknown key, a missing
    stage, or a malformed target, in which case this gate stamped an artifact
    that cannot run. Validating through the same entry point the runner uses
    keeps the gate from drifting away from execution.
    """

    try:
        from assert_ai.config import load_config, load_runtime_context
        from assert_ai.runner import STAGES
    except Exception as error:
        raise ReviewValidationError(
            "assert_ai is not importable, so the written config cannot be checked "
            "against the runtime schema. Install ASSERT into this environment "
            "(`pip install -e .`) and re-run. Skipping this check would stamp a "
            "config as approved without knowing whether it can run."
        ) from error

    try:
        raw = load_config(config_path)
        load_runtime_context(raw, config_path.resolve(), stage_modules=STAGES)
    except ReviewValidationError:
        raise
    except Exception as error:
        raise ReviewValidationError(
            f"config {config_path} is not valid against the ASSERT runtime schema: "
            f"{error}. This is the same check `assert-ai run` performs, so the "
            "config would fail at run time."
        ) from error


def _validate_behavior_identity(
    config: dict, review: dict[str, Any], config_path: Path
) -> None:
    """Require the config to describe the harm the review approved."""

    approved = str(review.get("harm_name", "")).strip()
    behavior = config.get("behavior")
    if not isinstance(behavior, dict):
        raise ReviewValidationError(
            f"config {config_path} has no `behavior` mapping, so it cannot be "
            f"matched against the approved harm {approved!r}."
        )
    written = str(behavior.get("name", "")).strip()
    if not written:
        raise ReviewValidationError(
            f"config {config_path} has no `behavior.name`, so it cannot be matched "
            f"against the approved harm {approved!r}."
        )
    if _normalize_item_name(written) != _normalize_item_name(approved):
        raise ReviewValidationError(
            f"config {config_path} declares behavior.name {written!r} but the "
            f"approved review is for {approved!r}. The approval covers one named "
            "harm; writing a config for a different one carries an approval that "
            "was never given for it."
        )


def _validate_retained_dimensions(
    config: dict, review: dict[str, Any], config_path: Path
) -> None:
    """Require the written dimensions to be the ones the user approved.

    The approval gate is the product claim of this workflow. If the config can
    carry a dimension set other than the reviewed one, the approval attests to a
    document rather than to the artifact that actually runs.

    ``behavior_categories`` are deliberately not compared: the config carries
    ``behavior_category_count`` and the categories themselves are generated at
    run time, so there are no names in the config to compare against.
    """

    for namespace, judge in (("judge_dimensions", True), ("test_dimensions", False)):
        approved = _approved_canonical_names(review, namespace)
        if not approved:
            continue
        written = _config_dimension_names(config, judge=judge)
        approved_keys = {_normalize_item_name(name) for name in approved}
        written_keys = {_normalize_item_name(name) for name in written}
        missing = sorted(approved_keys - written_keys)
        extra = sorted(written_keys - approved_keys)
        if not missing and not extra:
            continue
        problems = []
        if missing:
            problems.append(
                "approved but absent from the config: "
                + ", ".join(repr(name) for name in missing)
            )
        if extra:
            problems.append(
                "present in the config but never approved: "
                + ", ".join(repr(name) for name in extra)
            )
        raise ReviewValidationError(
            f"config {config_path} does not carry the approved {namespace} - "
            + "; ".join(problems)
            + ". The review approves a specific dimension set, so the config must "
            "contain exactly that set. Re-run the review cycle if the set needs "
            "to change."
        )


def _validate_single_risk(config: dict, config_path: Path) -> None:
    """Require one risk per suite, which the methodology treats as invariant.

    One-risk-per-suite is what makes a violation rate attributable: a suite
    covering two harms reports one number that belongs to neither.
    """

    behavior = config.get("behavior")
    if isinstance(behavior, list):
        raise ReviewValidationError(
            f"config {config_path} declares a list of behaviors. This methodology "
            "emits one risk per suite, because a suite covering several harms "
            "produces a violation rate that cannot be attributed to any one of "
            "them. Split it into one config per risk."
        )
    if not isinstance(behavior, dict):
        return
    for plural_key in ("behaviors", "risks", "harms"):
        if plural_key in config:
            raise ReviewValidationError(
                f"config {config_path} declares a top-level {plural_key!r} key. "
                "This methodology emits one risk per suite; split it into one "
                "config per risk."
            )
    name = behavior.get("name")
    if isinstance(name, list):
        raise ReviewValidationError(
            f"config {config_path} declares multiple behavior names. This "
            "methodology emits one risk per suite; split it into one config "
            "per risk."
        )


def post_write(review_path: Path, config_path: Path, stamp_path: Path) -> None:
    review = _validate_review_file(review_path, require_approval=True)
    if not stamp_path.is_file():
        raise ReviewValidationError(f"pre-write stamp not found: {stamp_path}")
    try:
        stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        raise ReviewValidationError(f"invalid pre-write stamp: {stamp_path}") from error
    if stamp.get("schema_version") != 1:
        raise ReviewValidationError("stamp schema_version must be 1")
    if stamp.get("review_path") != str(review_path.resolve()):
        raise ReviewValidationError("stamp review_path does not match this review")
    if stamp.get("config_path") != str(config_path.resolve()):
        raise ReviewValidationError("stamp config_path does not match this config")
    if stamp.get("review_sha256") != _sha256(review_path):
        raise ReviewValidationError("review changed after pre-write validation")
    if not config_path.is_file():
        raise ReviewValidationError(f"config was not written: {config_path}")

    config_hash = _sha256(config_path)
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise ReviewValidationError(f"config is not valid YAML: {config_path}") from error
    if not isinstance(config, dict):
        raise ReviewValidationError("config YAML must contain a top-level mapping")
    _reject_shadowing_judge_dimensions(config, config_path)
    # Everything above proves the file is well-formed YAML. These four prove it
    # is the artifact the review approved and that it can actually run, which is
    # what the approval is taken to mean downstream.
    _validate_single_risk(config, config_path)
    _validate_behavior_identity(config, review, config_path)
    _validate_retained_dimensions(config, review, config_path)
    _validate_runtime_schema(config_path)

    stamp["config_after_sha256"] = config_hash
    stamp["post_write_verified_at"] = datetime.now(timezone.utc).isoformat()
    _write_json(stamp_path, stamp)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render and validate N-pass ASSERT dimension-review ledgers."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    render_parser = subparsers.add_parser("render", help="Render Markdown tables from frontmatter")
    render_parser.add_argument("--review", required=True, type=Path)

    validate_parser = subparsers.add_parser("validate", help="Validate ledger and rendered tables")
    validate_parser.add_argument("--review", required=True, type=Path)
    validate_parser.add_argument("--require-approval", action="store_true")

    for command, help_text in (
        ("pre-write", "Validate approval and write a pre-config stamp"),
        ("post-write", "Verify the approved config was written after the stamp"),
    ):
        command_parser = subparsers.add_parser(command, help=help_text)
        command_parser.add_argument("--review", required=True, type=Path)
        command_parser.add_argument("--config", required=True, type=Path)
        command_parser.add_argument("--stamp", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "render":
            render_review(args.review)
            print(f"Rendered {args.review}")
        elif args.command == "validate":
            _validate_review_file(args.review, require_approval=args.require_approval)
            print(f"Validated {args.review}")
        else:
            stamp_path = args.stamp or _default_stamp_path(args.review)
            if args.command == "pre-write":
                pre_write(args.review, args.config, stamp_path)
                print(f"Validated approval and wrote {stamp_path}")
            else:
                post_write(args.review, args.config, stamp_path)
                print(f"Verified config write and updated {stamp_path}")
    except (OSError, ReviewValidationError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())