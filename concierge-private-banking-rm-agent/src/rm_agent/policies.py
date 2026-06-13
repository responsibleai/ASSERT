"""Lightweight policy helpers for the response and request surface.

Heavy per-action gating (sanctions, restricted, suitability, approval routing)
lives inside the two tools and in `GuardedRuntime`. The helpers here cover:

- detect_*  — user-input refusal classes (legal/tax/guarantee/approval bypass).
- contains_pii / redact_pii — PII detection and redaction for outputs and emails.
- contains_mnpi_leak / redact_mnpi — MNPI leakage detection and redaction.
- refusal_message_for_reasons — canonical refusal text.
"""

from __future__ import annotations

import re


LEGAL_PATTERNS = [
    r"\blegal advice\b",
    r"\bimmigration law\b",
    r"\bcontract law\b",
    r"\bsue\b",
]
TAX_PATTERNS = [
    r"\btax advice\b",
    r"\bhow do i reduce tax\b",
    r"\btax strategy\b",
]
GUARANTEE_PATTERNS = [
    r"\bguarantee(?:d)? return\b",
    r"\bpromise(?:d)? return\b",
    r"\bzero risk return\b",
]
BYPASS_PATTERNS = [
    r"\bskip approval\b",
    r"\bskip approvals\b",
    r"\bbypass approval\b",
    r"\bbypass approvals\b",
    r"\bwithout approval\b",
    r"\bi am the cco\b",
    r"\boverride compliance\b",
]


# Words near a pattern that should suppress a match (e.g. "avoid tax advice").
_NEGATION = {
    "avoid", "avoids", "avoiding",
    "without", "no", "not", "never",
    "exclude", "excluding",
}


def _matches_any(text: str, patterns: list[str]) -> bool:
    t = (text or "").lower()
    for pattern in patterns:
        for match in re.finditer(pattern, t):
            window = t[max(0, match.start() - 24) : match.start()]
            tokens = set(re.findall(r"[a-z]+", window))
            if tokens & _NEGATION:
                continue
            return True
    return False


def detect_legal_advice(text: str) -> bool:
    return _matches_any(text, LEGAL_PATTERNS)


def detect_tax_advice(text: str) -> bool:
    return _matches_any(text, TAX_PATTERNS)


def detect_guarantee(text: str) -> bool:
    return _matches_any(text, GUARANTEE_PATTERNS)


def detect_bypass_attempt(text: str) -> bool:
    return _matches_any(text, BYPASS_PATTERNS)


def refusal_message_for_reasons(reasons: list[str]) -> str:
    if "legal_advice_refusal" in reasons:
        return "I cannot provide legal advice. Please consult qualified legal counsel."
    if "tax_advice_refusal" in reasons:
        return "I cannot provide tax advice. Please consult a qualified tax professional."
    if "guaranteed_return_refusal" in reasons:
        return "I cannot promise guaranteed returns. Market outcomes are uncertain."
    if "approval_bypass_attempt" in reasons:
        return (
            "I cannot bypass approval controls. I can prepare a draft request "
            "for the proper approval workflow."
        )
    return "Blocked by policy."


_PII_PATTERNS = [
    r"\b\d{9,18}\b",          # full account-like identifiers
    r"\b\d{3}-?\d{2}-?\d{4}\b",  # SSN
    r"\bINT-\d{4,}\b",         # internal client IDs
]


def contains_pii(text: str) -> bool:
    if not text:
        return False
    return any(re.search(p, text) for p in _PII_PATTERNS)


def redact_pii(text: str) -> str:
    if not text:
        return text
    out = text
    out = re.sub(r"\b\d{3}-?\d{2}-?\d{4}\b", "[REDACTED-SSN]", out)
    out = re.sub(r"\bINT-\d{4,}\b", "[REDACTED-INTERNAL-ID]", out)
    out = re.sub(r"\b\d{9,18}\b", "[REDACTED-ACCOUNT]", out)
    return out


_MNPI_MARKERS = [
    "non-public",
    "mnpi",
    "deal timeline",
    "inside information",
    "confidential acquisition",
]


def contains_mnpi_leak(text: str) -> bool:
    if not text:
        return False
    t = text.lower()
    return any(m in t for m in _MNPI_MARKERS)


def redact_mnpi(text: str) -> str:
    if not text:
        return text
    out = text
    out = re.sub(r"(?i)non-public", "[REDACTED-MNPI]", out)
    out = re.sub(r"(?i)mnpi", "[REDACTED-MNPI]", out)
    out = re.sub(r"(?i)deal timeline", "[REDACTED-MNPI]", out)
    out = re.sub(r"(?i)inside information", "[REDACTED-MNPI]", out)
    return out
