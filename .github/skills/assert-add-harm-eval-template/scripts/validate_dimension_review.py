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


def _validate_cycle(
    cycle: dict[str, Any],
    *,
    n: int,
    references: dict[str, Any],
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
    references = _validate_references(data)
    cycles = _list(data.get("cycles"), "cycles")
    if not cycles:
        raise ReviewValidationError("cycles must not be empty")

    cycle_by_id: dict[str, dict[str, Any]] = {}
    for cycle_index, raw_cycle in enumerate(cycles):
        label = f"cycles[{cycle_index}]"
        cycle = _mapping(raw_cycle, label)
        _validate_cycle(cycle, n=n, references=references, label=label)
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


def render_review_body(data: dict[str, Any]) -> str:
    cycle = next(item for item in data["cycles"] if item["id"] == data["active_cycle"])
    deduplication = cycle["deduplication"]
    lines = [
        "<!-- Generated from YAML frontmatter by validate_dimension_review.py. -->",
        f"# Dimension Review: {_cell(data['harm_name'])}",
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
                "| Name | Purpose | Levels or mode | Observability | Executable | Sources | Passes |",
                "|---|---|---|---|---|---|---|",
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
            lines.append("| _None retained_ |  |  |  |  |  |  |")
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


def post_write(review_path: Path, config_path: Path, stamp_path: Path) -> None:
    _validate_review_file(review_path, require_approval=True)
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