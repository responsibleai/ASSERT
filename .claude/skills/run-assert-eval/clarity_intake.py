"""Convert Clarity failure docs into ASSERT-ready candidate behaviors.

This module reads a project's ``.clarity-protocol/failures/`` directory (produced
by the Clarity agent, https://github.com/microsoft/clarity-agent) and turns each
discovered failure mode into a candidate ASSERT behavior: a name, a testable
description, a severity/priority, and candidate ``test_set.stratify.dimensions``
mined from the failure's variants and failure-chain conditions.

Design notes
------------
* ``.clarity-protocol/`` markdown files are the source of truth. Any JSON this
  module emits is a disposable cache, never authoritative.
* Parsing is tolerant: unknown severity labels or missing headers degrade to a
  flagged candidate (``warnings`` populated) rather than crashing or being
  silently dropped.
* ASSERT science guidance is one atomic behavior per eval. A single failure mode
  is usually one behavior, but a doc that clearly bundles several independently
  testable behaviors is flagged (``multi_behavior``) with ``suggested_splits`` so
  the triage step can surface the split to the user.

The module is dependency-free (stdlib only) and safe to import from the skill.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# Severity ranking, highest first. Used to collapse ranges (e.g. "Medium-Critical")
# to their maximum and to sort candidates for triage.
SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1}
PRIORITY_BY_SEVERITY = {
    "critical": "P1",
    "high": "P2",
    "medium": "P3",
    "low": "P4",
}
_KNOWN_SEVERITIES = "|".join(SEVERITY_RANK)

# `1. **[Title](failure-01-slug.md)** (Severity) Summary text...`
_INDEX_ENTRY = re.compile(
    r"^\s*\d+\.\s+\*\*\[(?P<title>[^\]]+)\]\((?P<path>[^)]+)\)\*\*"
    r"\s*(?:\((?P<sev>[^)]*)\))?\s*(?P<summary>.*)$"
)
_SECTION = re.compile(r"^##\s+(?P<header>.+?)\s*$", re.MULTILINE)
_DOC_TITLE = re.compile(r"^#\s+Failure:\s*(?P<title>.+?)\s*$", re.MULTILINE)
_SEVERITY_LINE = re.compile(r"\*\*Severity:\*\*\s*(?P<body>.+)")
_BOLD_LEAD = re.compile(r"\*\*(?P<lead>[^*]+?)\*\*")
_ITALIC_LABEL = re.compile(r"\*(?:Intervention point|Branch)\s*\((?P<label>[^)]+)\)")
_VARIANT_LEAD = re.compile(r"^\s*-\s+\*(?P<label>[^*:]+):\*", re.MULTILINE)
# Italic bullet leads that are structural annotations, not test conditions.
_CHAIN_NOISE = re.compile(r"^(?:Intervention point|Branch|Observation)\b", re.IGNORECASE)

# --- Monolithic format ("## failure-NN — Title" sections in one failures.md) ---
# A section header identifying one failure, e.g. "failure-01 — Identity-gate bypass".
# Accepts em dash, en dash, or hyphen as the title separator.
_MONO_HEADER = re.compile(r"^failure-(?P<num>\d+)\s*[\u2014\u2013-]\s*(?P<title>.+?)\s*$")
# A bold lead block, e.g. "**Summary.**", "**Variants (elicitation_variant).**",
# "**Severity: Critical**". Everything between the ``**`` markers is the lead.
_MONO_BOLD_LEAD = re.compile(r"^\*\*(?P<lead>[^*\n]+?)\*\*\.?\s*", re.MULTILINE)
# The dimension name a Variants block declares, e.g. "Variants (elicitation_variant)".
_MONO_VARIANTS_DIM = re.compile(r"Variants\s*\((?P<dim>[^)]+)\)", re.IGNORECASE)


@dataclass
class CandidateBehavior:
    """One ASSERT-ready behavior derived from a Clarity failure mode."""

    name: str
    description: str
    severity: str
    priority: str
    source_doc: str
    candidate_dimensions: list[dict] = field(default_factory=list)
    multi_behavior: bool = False
    suggested_splits: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def normalize_severity(raw: str | None) -> tuple[str, list[str]]:
    """Return (severity_label, warnings).

    Collapses ranges to their maximum ("Medium-Critical" -> "Critical",
    "Ranges from Medium ... to Critical" -> "Critical"). Unknown/empty input
    degrades to "Unknown" with a warning rather than raising.
    """

    warnings: list[str] = []
    if not raw or not raw.strip():
        return "Unknown", ["missing severity; defaulted to Unknown"]
    labels = re.findall(_KNOWN_SEVERITIES, raw, re.IGNORECASE)
    if not labels:
        return "Unknown", [f"unrecognized severity {raw.strip()!r}; defaulted to Unknown"]
    top = max(labels, key=lambda label: SEVERITY_RANK[label.lower()])
    return top.capitalize(), warnings


def severity_to_priority(severity: str) -> str:
    """Map a normalized severity label to a P1-P4 priority."""

    return PRIORITY_BY_SEVERITY.get(severity.lower(), "P3")


def parse_failures_index(text: str) -> list[dict]:
    """Parse ``failures.md`` into a list of index entries.

    Each entry: ``{index, title, doc_path, severity, priority, summary, status,
    warnings}``. The status column tracks the most recent ``## Section`` header
    (e.g. "Managed") the entry appeared under.
    """

    entries: list[dict] = []
    status = ""
    for i, line in enumerate(text.splitlines(), start=1):
        section = _SECTION.match(line)
        if section:
            status = section.group("header").strip()
            continue
        match = _INDEX_ENTRY.match(line)
        if not match:
            continue
        severity, warnings = normalize_severity(match.group("sev"))
        entries.append(
            {
                "index": len(entries) + 1,
                "line": i,
                "title": match.group("title").strip(),
                "doc_path": match.group("path").strip(),
                "severity": severity,
                "priority": severity_to_priority(severity),
                "summary": match.group("summary").strip(),
                "status": status,
                "warnings": warnings,
            }
        )
    return entries


def _split_sections(text: str) -> dict[str, str]:
    """Split a failure doc into ``{header: body}`` keyed by ``## Header``."""

    sections: dict[str, str] = {}
    matches = list(_SECTION.finditer(text))
    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        sections[match.group("header").strip()] = text[start:end].strip()
    return sections


def _extract_variants(observations: str) -> list[str]:
    """Pull the ``**Variants:**`` bullet list out of the Observations section."""

    marker = re.search(r"\*\*Variants:\*\*", observations)
    if not marker:
        return []
    tail = observations[marker.end():]
    variants: list[str] = []
    for line in tail.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            variants.append(stripped[2:].strip())
        elif variants and not stripped:
            continue
        elif variants and not line.startswith(" ") and not stripped.startswith("-"):
            # A new non-indented, non-bullet line ends the variants list.
            break
    return [v for v in variants if v]


def _extract_chain_conditions(failure_chain: str) -> list[str]:
    """Mine failure-chain intervention/branch/variant labels (test conditions)."""

    labels: list[str] = []
    labels.extend(m.group("label").strip() for m in _ITALIC_LABEL.finditer(failure_chain))
    for m in _VARIANT_LEAD.finditer(failure_chain):
        label = m.group("label").strip()
        # Skip structural annotations (Intervention point/Branch/Observation);
        # those parenthetical labels are already captured by _ITALIC_LABEL.
        if not _CHAIN_NOISE.match(label):
            labels.append(label)
    # De-duplicate while preserving order.
    seen: set[str] = set()
    unique: list[str] = []
    for label in labels:
        key = label.lower()
        if key not in seen:
            seen.add(key)
            unique.append(label)
    return unique


def derive_dimensions(variants: list[str], chain_conditions: list[str]) -> list[dict]:
    """Turn variants and chain conditions into candidate stratify dimensions.

    Variants are the highest-value source: each variant is a distinct way the
    failure is elicited, so they map to one dimension with the variants as its
    values. Failure-chain condition labels seed a second condition dimension.
    """

    dimensions: list[dict] = []
    if variants:
        dimensions.append(
            {
                "name": "elicitation_variant",
                "description": (
                    "How the failure is elicited. Derived from the Clarity "
                    "failure doc's Variants list; each value is a distinct route "
                    "to the same failure."
                ),
                "values": variants,
            }
        )
    if chain_conditions:
        dimensions.append(
            {
                "name": "interaction_condition",
                "description": (
                    "Conditions under which the failure manifests, mined from the "
                    "failure chain's intervention points and branches."
                ),
                "values": chain_conditions,
            }
        )
    return dimensions


def _detect_bundle(title: str, summary: str, sections: dict[str, str]) -> tuple[bool, list[str]]:
    """Heuristically detect a doc that bundles several testable behaviors.

    Returns ``(multi_behavior, suggested_splits)``. Conservative: only flags when
    there is a clear "X and Y" span or an explicit ``## Key Risks`` list of
    several bolded, independently mitigated risks.
    """

    splits: list[str] = []
    key_risks = sections.get("Key Risks", "")
    if key_risks:
        for line in key_risks.splitlines():
            lead = _BOLD_LEAD.match(line.strip())
            if lead:
                splits.append(lead.group("lead").strip().rstrip("."))

    title_has_and = bool(re.search(r"\b(and|&|/|,)\b", title))
    bundled = len(splits) >= 2 or (title_has_and and len(splits) >= 1)
    if bundled and not splits:
        splits = [part.strip() for part in re.split(r"\band\b", title) if part.strip()]
    return bundled, splits


def parse_failure_doc(text: str, doc_path: str) -> dict:
    """Parse a single ``failure-NN-*.md`` doc into structured fields."""

    warnings: list[str] = []
    title_match = _DOC_TITLE.search(text)
    title = title_match.group("title").strip() if title_match else ""
    if not title:
        warnings.append("missing '# Failure:' title header")

    sections = _split_sections(text)
    summary = sections.get("Summary", "").strip()
    if not summary:
        warnings.append("missing '## Summary' section")

    observations = sections.get("Observations", "")
    doc_severity = "Unknown"
    if observations:
        sev_line = _SEVERITY_LINE.search(observations)
        if sev_line:
            doc_severity, sev_warnings = normalize_severity(sev_line.group("body"))
            warnings.extend(sev_warnings)

    variants = _extract_variants(observations)
    chain_conditions = _extract_chain_conditions(sections.get("Failure Chain", ""))
    multi_behavior, suggested_splits = _detect_bundle(title, summary, sections)

    return {
        "title": title,
        "summary": summary,
        "doc_severity": doc_severity,
        "variants": variants,
        "chain_conditions": chain_conditions,
        "multi_behavior": multi_behavior,
        "suggested_splits": suggested_splits,
        "warnings": warnings,
    }


def _slug_to_name(doc_path: str, title: str) -> str:
    """Derive a short behavior name from the doc slug, falling back to the title."""

    stem = Path(doc_path).stem
    stem = re.sub(r"^failure-\d+-", "", stem)
    stem = stem.replace("-", "_").strip("_")
    if stem:
        return stem
    return re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_") or "unnamed_behavior"


def _split_mono_bold_blocks(body: str) -> list[tuple[str, str]]:
    """Split a monolithic failure body into ``(lead, block_body)`` pairs.

    Each block starts at a ``**Lead.**`` marker (Summary, Failure chain, Variants,
    Interaction condition, Intervention points, Severity, ...) and runs until the
    next such marker. Order is preserved.
    """

    blocks: list[tuple[str, str]] = []
    matches = list(_MONO_BOLD_LEAD.finditer(body))
    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(body)
        lead = match.group("lead").strip().rstrip(".").strip()
        blocks.append((lead, body[start:end].strip()))
    return blocks


def _extract_mono_variants(block_body: str) -> list[str]:
    """Pull the bullet list out of a monolithic ``**Variants (...).**`` block."""

    variants: list[str] = []
    for line in block_body.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            variants.append(stripped[2:].strip())
        elif variants and not stripped:
            continue
        elif variants and not stripped.startswith("-"):
            break
    return [v for v in variants if v]


def parse_monolithic_failures(text: str) -> list[CandidateBehavior]:
    """Parse a single monolithic ``failures.md`` (``## failure-NN — Title`` sections).

    This is the format the Clarity agent ships in this repo: one file, each failure
    a ``## failure-NN — Title`` section with ``**Severity: X**``, ``**Summary.**``,
    ``**Variants (<dim>).**`` bullets, ``**Interaction condition.**``, and
    ``**Intervention points.**`` blocks. Returns one candidate per failure section.
    Tolerant: missing fields degrade to warnings rather than raising.
    """

    sections = _split_sections(text)
    candidates: list[CandidateBehavior] = []
    for header, section_body in sections.items():
        head = _MONO_HEADER.match(header.strip())
        if not head:
            continue  # e.g. "Priority summary" or other non-failure sections
        title = head.group("title").strip()
        warnings: list[str] = []

        blocks = _split_mono_bold_blocks(section_body)
        severity_raw: str | None = None
        summary = ""
        variants: list[str] = []
        variant_dim = "elicitation_variant"
        conditions: list[str] = []
        for lead, block_body in blocks:
            low = lead.lower()
            if low.startswith("severity"):
                # "Severity: Critical" -> take the text after the colon.
                severity_raw = lead.split(":", 1)[1] if ":" in lead else block_body
            elif low.startswith("summary"):
                summary = block_body
            elif low.startswith("variants"):
                dim_match = _MONO_VARIANTS_DIM.search(lead)
                if dim_match:
                    variant_dim = dim_match.group("dim").strip()
                variants = _extract_mono_variants(block_body)
            elif low.startswith("interaction condition"):
                if block_body:
                    conditions.append(block_body.replace("\n", " ").strip())

        severity, sev_warnings = normalize_severity(severity_raw)
        warnings.extend(sev_warnings)
        if not summary:
            warnings.append("missing '**Summary.**' block")

        dimensions: list[dict] = []
        if variants:
            dimensions.append(
                {
                    "name": variant_dim,
                    "description": (
                        "How the failure is elicited. Derived from the Clarity "
                        "failure's Variants list; each value is a distinct route "
                        "to the same failure."
                    ),
                    "values": variants,
                }
            )
        else:
            warnings.append(
                "no variants found; stratify dimensions must be authored manually"
            )
        if len(conditions) > 1:
            dimensions.append(
                {
                    "name": "interaction_condition",
                    "description": (
                        "Conditions under which the failure manifests, mined from "
                        "the failure's Interaction condition block."
                    ),
                    "values": conditions,
                }
            )

        source = f"failures.md#failure-{head.group('num')}"
        name = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_") or "unnamed_behavior"
        candidates.append(
            CandidateBehavior(
                name=name,
                description=summary or title,
                severity=severity,
                priority=severity_to_priority(severity),
                source_doc=source,
                candidate_dimensions=dimensions,
                warnings=warnings,
            )
        )
    return candidates


def build_candidate_behaviors(protocol_dir: str | Path) -> list[CandidateBehavior]:
    """Read a ``.clarity-protocol`` directory and build candidate behaviors.

    ``protocol_dir`` may point at the ``.clarity-protocol`` directory itself, its
    ``failures/`` subdirectory, or the project root. Two ``failures.md`` layouts
    are supported: an *index* format (numbered links to per-failure ``failure-NN-*.md``
    docs) and a *monolithic* format (one file with ``## failure-NN — Title``
    sections). Missing individual docs are tolerated: the index entry still yields a
    candidate, flagged with a warning.
    """

    failures_dir = _resolve_failures_dir(protocol_dir)
    index_path = failures_dir / "failures.md"
    if not index_path.is_file():
        raise FileNotFoundError(f"no failures.md under {failures_dir}")

    text = index_path.read_text(encoding="utf-8")
    entries = parse_failures_index(text)
    if not entries:
        # No index links -> assume the monolithic single-file format.
        candidates = parse_monolithic_failures(text)
        candidates.sort(key=lambda c: (_priority_sort_key(c.priority), c.name))
        return candidates
    candidates: list[CandidateBehavior] = []
    for entry in entries:
        warnings = list(entry["warnings"])
        doc_path = failures_dir / entry["doc_path"]
        description = entry["summary"]
        dimensions: list[dict] = []
        multi_behavior = False
        suggested_splits: list[str] = []

        if doc_path.is_file():
            doc = parse_failure_doc(doc_path.read_text(encoding="utf-8"), str(doc_path))
            warnings.extend(doc["warnings"])
            if doc["summary"]:
                description = doc["summary"]
            dimensions = derive_dimensions(doc["variants"], doc["chain_conditions"])
            multi_behavior = doc["multi_behavior"]
            suggested_splits = doc["suggested_splits"]
            if not dimensions:
                warnings.append(
                    "no variants or failure-chain conditions found; "
                    "dimensions must be authored manually"
                )
        else:
            warnings.append(f"failure doc not found: {entry['doc_path']}")

        candidates.append(
            CandidateBehavior(
                name=_slug_to_name(entry["doc_path"], entry["title"]),
                description=description,
                severity=entry["severity"],
                priority=entry["priority"],
                source_doc=entry["doc_path"],
                candidate_dimensions=dimensions,
                multi_behavior=multi_behavior,
                suggested_splits=suggested_splits,
                warnings=warnings,
            )
        )

    candidates.sort(key=lambda c: (_priority_sort_key(c.priority), c.name))
    return candidates


def _priority_sort_key(priority: str) -> int:
    match = re.search(r"\d+", priority)
    return int(match.group()) if match else 99


def _resolve_failures_dir(protocol_dir: str | Path) -> Path:
    """Locate the ``failures/`` directory from any reasonable starting point."""

    path = Path(protocol_dir)
    candidates = [
        path,
        path / "failures",
        path / ".clarity-protocol" / "failures",
    ]
    for candidate in candidates:
        if (candidate / "failures.md").is_file():
            return candidate
    # Default to the most specific guess for a clear error message upstream.
    return path / "failures" if path.name != "failures" else path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "protocol_dir",
        help="Path to .clarity-protocol, its failures/ dir, or the project root.",
    )
    args = parser.parse_args(argv)
    candidates = build_candidate_behaviors(args.protocol_dir)
    print(json.dumps([c.to_dict() for c in candidates], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
