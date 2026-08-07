"""Tests for clarity_intake: Clarity failure docs -> ASSERT candidate behaviors.

Fixtures under ``tests/fixtures/`` are real Clarity output (the clarity-agent repo
dogfoods its own ``.clarity-protocol/``) plus a small synthetic set that exercises
tolerant-degradation paths (unknown severity label, missing doc on disk).

Run standalone (does not touch the repo's own suite):
    python -m pytest .claude/skills/run-assert-eval/tests/test_clarity_intake.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make the skill dir importable without installing anything.
SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

import clarity_intake as ci  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
REAL = FIXTURES / "clarity-protocol"
SYNTHETIC = FIXTURES / "synthetic"
MONOLITHIC = FIXTURES / "monolithic"


# --- normalize_severity / priority ------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Critical", "Critical"),
        ("high", "High"),
        ("Medium", "Medium"),
        ("Low", "Low"),
        # Range collapses to the maximum severity.
        ("Medium\u2013Critical", "Critical"),  # en dash
        ("Medium-Critical", "Critical"),  # hyphen
        ("Ranges from Medium (cost) to Critical (data breach)", "Critical"),
    ],
)
def test_normalize_severity_collapses_ranges_to_max(raw, expected):
    severity, warnings = ci.normalize_severity(raw)
    assert severity == expected
    assert warnings == []


def test_normalize_severity_unknown_degrades_with_warning():
    severity, warnings = ci.normalize_severity("Spicy")
    assert severity == "Unknown"
    assert warnings and "unrecognized severity" in warnings[0]


def test_normalize_severity_empty_degrades_with_warning():
    severity, warnings = ci.normalize_severity("")
    assert severity == "Unknown"
    assert warnings


def test_severity_to_priority_mapping():
    assert ci.severity_to_priority("Critical") == "P1"
    assert ci.severity_to_priority("High") == "P2"
    assert ci.severity_to_priority("Medium") == "P3"
    assert ci.severity_to_priority("Low") == "P4"


# --- index parsing ----------------------------------------------------------


def test_parse_failures_index_reads_all_entries():
    text = (REAL / "failures" / "failures.md").read_text(encoding="utf-8")
    entries = ci.parse_failures_index(text)
    assert len(entries) == 7
    first = entries[0]
    assert first["title"] == "User disengagement"
    assert first["doc_path"] == "failure-01-user-disengagement.md"
    assert first["severity"] == "Critical"
    assert first["priority"] == "P1"
    assert first["status"] == "Managed"


def test_parse_failures_index_severity_range_entry():
    text = (REAL / "failures" / "failures.md").read_text(encoding="utf-8")
    entries = ci.parse_failures_index(text)
    op = next(e for e in entries if "operational" in e["doc_path"])
    # "Medium-Critical" range -> max severity Critical -> P1.
    assert op["severity"] == "Critical"
    assert op["priority"] == "P1"


# --- doc parsing: variants -> dimensions ------------------------------------


def test_parse_failure_doc_extracts_variants():
    text = (REAL / "failures" / "failure-01-user-disengagement.md").read_text(
        encoding="utf-8"
    )
    doc = ci.parse_failure_doc(text, "failure-01-user-disengagement.md")
    assert doc["title"].startswith("Users resist")
    assert doc["summary"]
    assert doc["doc_severity"] == "Critical"
    assert len(doc["variants"]) == 7
    assert "Wrong calibration of challenge intensity" in doc["variants"]
    assert doc["warnings"] == []


def test_derive_dimensions_maps_variants_to_elicitation_dimension():
    text = (REAL / "failures" / "failure-01-user-disengagement.md").read_text(
        encoding="utf-8"
    )
    doc = ci.parse_failure_doc(text, "failure-01-user-disengagement.md")
    dims = ci.derive_dimensions(doc["variants"], doc["chain_conditions"])
    variant_dim = next(d for d in dims if d["name"] == "elicitation_variant")
    assert variant_dim["values"] == doc["variants"]
    # Chain-condition dimension is present and free of structural noise.
    cond_dim = next(d for d in dims if d["name"] == "interaction_condition")
    assert "Observation" not in cond_dim["values"]
    assert not any(v.startswith("Intervention point") for v in cond_dim["values"])


# --- doc parsing: bundle detection / atomicity ------------------------------


def test_bundle_detection_flags_operational_risks_doc():
    text = (REAL / "failures" / "failure-07-operational-risks.md").read_text(
        encoding="utf-8"
    )
    doc = ci.parse_failure_doc(text, "failure-07-operational-risks.md")
    assert doc["multi_behavior"] is True
    assert len(doc["suggested_splits"]) >= 2
    assert any("prompt injection" in s.lower() for s in doc["suggested_splits"])


def test_atomic_doc_not_flagged_as_bundle():
    text = (REAL / "failures" / "failure-01-user-disengagement.md").read_text(
        encoding="utf-8"
    )
    doc = ci.parse_failure_doc(text, "failure-01-user-disengagement.md")
    assert doc["multi_behavior"] is False
    assert doc["suggested_splits"] == []


# --- end-to-end build -------------------------------------------------------


def test_build_candidate_behaviors_sorts_by_priority():
    candidates = ci.build_candidate_behaviors(REAL)
    assert len(candidates) == 7
    priorities = [c.priority for c in candidates]
    # Sorted ascending P1 -> P3 (P1 numerically smallest).
    assert priorities == sorted(priorities, key=lambda p: int(p[1:]))
    assert candidates[0].priority == "P1"


def test_build_candidate_behaviors_accepts_project_root_or_failures_dir():
    from_root = ci.build_candidate_behaviors(REAL)
    from_failures = ci.build_candidate_behaviors(REAL / "failures")
    assert [c.name for c in from_root] == [c.name for c in from_failures]


def test_build_missing_index_raises():
    with pytest.raises(FileNotFoundError):
        ci.build_candidate_behaviors(FIXTURES / "does-not-exist")


# --- monolithic single-file format ------------------------------------------


def test_monolithic_format_yields_one_candidate_per_failure():
    candidates = ci.build_candidate_behaviors(MONOLITHIC)
    # 3 failure-NN sections; the "Priority summary" section is ignored.
    assert len(candidates) == 3
    names = [c.name for c in candidates]
    assert "identity_gate_bypass_on_high_risk_actions" in names
    assert "prompt_injection_via_untrusted_tool_content" in names


def test_monolithic_format_parses_severity_summary_and_variants():
    candidates = ci.build_candidate_behaviors(MONOLITHIC)
    identity = next(
        c for c in candidates if c.name == "identity_gate_bypass_on_high_risk_actions"
    )
    assert identity.severity == "Critical"
    assert identity.priority == "P1"
    assert identity.description.startswith("The agent executes a high-risk action")
    dim = next(d for d in identity.candidate_dimensions if d["name"] == "elicitation_variant")
    assert len(dim["values"]) == 3
    assert any("Urgency" in v for v in dim["values"])
    assert identity.warnings == []


def test_monolithic_severity_with_trailing_parenthetical():
    candidates = ci.build_candidate_behaviors(MONOLITHIC)
    injection = next(
        c for c in candidates if c.name == "prompt_injection_via_untrusted_tool_content"
    )
    # "**Severity: High** (amplifier ...)" -> High, ignoring the parenthetical.
    assert injection.severity == "High"
    assert injection.priority == "P2"


def test_monolithic_malformed_failure_degrades_without_crashing():
    candidates = ci.build_candidate_behaviors(MONOLITHIC)
    malformed = next(
        c for c in candidates if c.name == "no_variants_failure_with_missing_summary"
    )
    assert malformed.severity == "Unknown"
    assert any("unrecognized severity" in w for w in malformed.warnings)
    assert any("Summary" in w for w in malformed.warnings)
    assert any("no variants" in w for w in malformed.warnings)


def test_monolithic_sorted_by_priority():
    candidates = ci.build_candidate_behaviors(MONOLITHIC)
    priorities = [c.priority for c in candidates]
    assert priorities == sorted(priorities, key=lambda p: int(p[1:]))


# --- tolerant degradation ---------------------------------------------------


def test_missing_doc_still_yields_flagged_candidate():
    candidates = ci.build_candidate_behaviors(SYNTHETIC)
    missing = next(c for c in candidates if c.name == "missing")
    # Falls back to the index summary; flagged, never dropped.
    assert missing.description
    assert any("not found" in w for w in missing.warnings)


def test_malformed_doc_degrades_without_crashing():
    candidates = ci.build_candidate_behaviors(SYNTHETIC)
    names = {c.name for c in candidates}
    assert {"malformed", "missing"} <= names  # nothing dropped
    malformed = next(c for c in candidates if c.name == "malformed")
    assert malformed.severity == "Unknown"
    assert any("unrecognized severity" in w for w in malformed.warnings)
    assert any("missing '## Summary'" in w for w in malformed.warnings)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
