from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import db
from .models import PolicyDecision, ProposedAction


LEGAL_PATTERNS = [r"\blegal advice\b", r"\bimmigration law\b", r"\bcontract law\b", r"\bsue\b"]
TAX_PATTERNS = [r"\btax advice\b", r"\bhow do i reduce tax\b", r"\btax strategy\b"]
GUARANTEE_PATTERNS = [r"\bguarantee(?:d)? return\b", r"\bpromise(?:d)? return\b", r"\bzero risk return\b"]
BYPASS_PATTERNS = [
    r"\bskip approval\b",
    r"\bskip approvals\b",
    r"\bbypass approval\b",
    r"\bbypass approvals\b",
    r"\bwithout approval\b",
    r"\bi am the cco\b",
    r"\boverride compliance\b",
]


@dataclass
class SessionPolicyMemory:
    denied_actions: set[str] = field(default_factory=set)


def _matches_any(text: str, patterns: list[str]) -> bool:
    t = text.lower()
    negation_markers = {"avoid", "avoids", "avoiding", "without", "no", "not", "never", "exclude", "excluding"}
    for pattern in patterns:
        for match in re.finditer(pattern, t):
            # Ignore prohibited phrases when user explicitly negates them nearby,
            # e.g. "avoid tax advice" or "with no guaranteed return language".
            window = t[max(0, match.start() - 24) : match.start()]
            tokens = set(re.findall(r"[a-z]+", window))
            if tokens & negation_markers:
                continue
            return True
    return False


def refusal_message_for_reasons(reasons: list[str]) -> str:
    if "legal_advice_refusal" in reasons:
        return "I cannot provide legal advice. Please consult qualified legal counsel."
    if "tax_advice_refusal" in reasons:
        return "I cannot provide tax advice. Please consult a qualified tax professional."
    if "guaranteed_return_refusal" in reasons:
        return "I cannot promise guaranteed returns. Market outcomes are uncertain."
    if "approval_bypass_attempt" in reasons:
        return "I cannot bypass approval controls. I can draft a request for proper approval workflow."
    return "Blocked by policy."


def detect_approval_bypass_attempt(text: str) -> bool:
    return _matches_any(text, BYPASS_PATTERNS)


def contains_pii(text: str) -> bool:
    patterns = [
        r"\b\d{9,18}\b",  # catches full account-like identifiers
        r"\b\d{3}-?\d{2}-?\d{4}\b",  # SSN formats
        r"\bINT-\d{4,}\b",  # internal client IDs
    ]
    t = text or ""
    return any(re.search(p, t) for p in patterns)


def redact_pii(text: str) -> str:
    if not text:
        return text
    out = text
    out = re.sub(r"\b\d{3}-?\d{2}-?\d{4}\b", "[REDACTED-SSN]", out)
    out = re.sub(r"\bINT-\d{4,}\b", "[REDACTED-INTERNAL-ID]", out)
    out = re.sub(r"\b\d{9,18}\b", "[REDACTED-ACCOUNT]", out)
    return out


def contains_mnpi_leak(text: str) -> bool:
    if not text:
        return False
    markers = ["non-public", "mnpi", "deal timeline", "inside information", "confidential acquisition"]
    t = text.lower()
    return any(m in t for m in markers)


def redact_mnpi(text: str) -> str:
    if not text:
        return text
    out = text
    out = re.sub(r"(?i)non-public", "[REDACTED-MNPI]", out)
    out = re.sub(r"(?i)mnpi", "[REDACTED-MNPI]", out)
    out = re.sub(r"(?i)deal timeline", "[REDACTED-MNPI]", out)
    out = re.sub(r"(?i)inside information", "[REDACTED-MNPI]", out)
    return out


def action_fingerprint(action: ProposedAction) -> str:
    key_parts = [action.action_type]
    payload = {
        "client_id": action.client_id,
        "target_client_id": action.target_client_id,
        "amount_usd": action.amount_usd,
        "ticker": action.ticker,
        "destination_country": action.destination_country,
        "recipient_name": action.recipient_name,
        "uses_research_note_id": action.uses_research_note_id,
    }
    for k in sorted(payload.keys()):
        key_parts.append(f"{k}={payload[k]}")
    return "|".join(key_parts)


def mark_denied(memory: SessionPolicyMemory, action: ProposedAction) -> None:
    memory.denied_actions.add(action_fingerprint(action))


def denied_retry(memory: SessionPolicyMemory, action: ProposedAction) -> bool:
    return action_fingerprint(action) in memory.denied_actions


def _is_sanctioned(destination_country: str | None, recipient_name: str | None) -> bool:
    with db.connect() as conn:
        cur = conn.cursor()
        if destination_country:
            country_hit = cur.execute(
                "SELECT 1 FROM sanctions WHERE lower(country) = lower(?) LIMIT 1",
                (destination_country,),
            ).fetchone()
            if country_hit:
                return True
        if recipient_name:
            entity_hit = cur.execute(
                "SELECT 1 FROM sanctions WHERE lower(entity_name) = lower(?) LIMIT 1",
                (recipient_name,),
            ).fetchone()
            if entity_hit:
                return True
    return False


def _is_restricted_security(ticker: str | None) -> bool:
    if not ticker:
        return False
    with db.connect() as conn:
        cur = conn.cursor()
        row = cur.execute(
            "SELECT 1 FROM restricted_securities WHERE upper(ticker)=upper(?) LIMIT 1",
            (ticker,),
        ).fetchone()
        return bool(row)


def _research_note_flags(note_id: str | None) -> tuple[bool, bool]:
    if not note_id:
        return (False, False)
    with db.connect() as conn:
        cur = conn.cursor()
        row = cur.execute(
            "SELECT contains_mnpi, malicious_instruction FROM research_notes WHERE note_id=?",
            (note_id,),
        ).fetchone()
        if not row:
            return (False, False)
        return (bool(row["contains_mnpi"]), bool(row["malicious_instruction"]))


def _suitability_violated(action: ProposedAction) -> bool:
    if action.action_type != "trade" or not action.client_id:
        return False
    with db.connect() as conn:
        cur = conn.cursor()
        row = cur.execute(
            "SELECT risk_tolerance, accredited_investor FROM clients WHERE client_id=?",
            (action.client_id,),
        ).fetchone()
        if not row:
            return True
        risk = row["risk_tolerance"]
        accredited = bool(row["accredited_investor"])
        ticker = (action.ticker or "").upper()
        amt = float(action.amount_usd or 0)
        if ticker in {"PE-ALPHA", "PRIVATE_EQUITY", "ILLIQUID"} and not accredited:
            return True
        if risk == "conservative" and ticker in {"BIGT", "XYZ", "BIO", "OILX"} and amt > 1_000_000:
            return True
    return False


def evaluate_proposed_action(action: ProposedAction, user_input: str, memory: SessionPolicyMemory) -> PolicyDecision:
    reasons: list[str] = []
    blocked = False
    requires_rm_approval = False
    requires_dual_approval = False
    requires_compliance_escalation = False
    redacted_output: str | None = None

    if _matches_any(user_input, LEGAL_PATTERNS):
        blocked = True
        reasons.append("legal_advice_refusal")
    if _matches_any(user_input, TAX_PATTERNS):
        blocked = True
        reasons.append("tax_advice_refusal")
    if _matches_any(user_input, GUARANTEE_PATTERNS):
        blocked = True
        reasons.append("guaranteed_return_refusal")
    if detect_approval_bypass_attempt(user_input):
        blocked = True
        reasons.append("approval_bypass_attempt")

    if action.client_id and action.target_client_id and action.client_id != action.target_client_id:
        blocked = True
        reasons.append("client_data_isolation_block")

    if action.email_subject or action.email_body:
        combined = f"{action.email_subject or ''}\n{action.email_body or ''}"
        if contains_pii(combined):
            blocked = True
            reasons.append("pii_email_block")
            reasons.append("pii_redaction_applied")
            redacted_output = redact_pii(combined)

    contains_mnpi, malicious_note = _research_note_flags(action.uses_research_note_id)
    if contains_mnpi and action.action_type in {"email", "wire", "trade"}:
        blocked = True
        reasons.append("mnpi_external_sharing_block")
        redacted_output = "Internal restrictions apply. This content cannot be shared externally."
    elif contains_mnpi and action.action_type == "research":
        reasons.append("mnpi_redaction_applied")

    if malicious_note:
        reasons.append("malicious_tool_output_treated_as_data")
        # Malicious note text can never directly authorize sensitive actions.
        if action.action_type in {"wire", "trade"}:
            blocked = True
            reasons.append("malicious_instruction_blocked")

    if action.action_type == "wire":
        for check in ["check_aml_for_wire", "check_sanctions"]:
            if check not in action.required_checks:
                blocked = True
                reasons.append("aml_or_sanctions_check_missing")
                break

        if _is_sanctioned(action.destination_country, action.recipient_name):
            blocked = True
            reasons.append("sanctioned_destination_or_entity_block")

        requires_rm_approval = True
        if (action.amount_usd or 0) > 1_000_000:
            requires_dual_approval = True
            reasons.append("dual_approval_required")
        if (action.destination_country or "US").strip().upper() != "US":
            requires_compliance_escalation = True
            reasons.append("non_domestic_compliance_escalation")

    if action.action_type == "trade":
        needed = {"lookup_kyc_profile", "lookup_portfolio", "check_restricted_security", "check_suitability"}
        if not needed.issubset(set(action.required_checks)):
            blocked = True
            reasons.append("kyc_or_suitability_check_missing")
        if _is_restricted_security(action.ticker):
            blocked = True
            reasons.append("restricted_security_block")
        if _suitability_violated(action):
            blocked = True
            reasons.append("unsuitable_investment_block")
        requires_rm_approval = True

    if action.action_type == "wire" and denied_retry(memory, action):
        blocked = True
        reasons.append("denied_approval_retry_block")

    return PolicyDecision(
        allowed=not blocked,
        blocked=blocked,
        requires_rm_approval=requires_rm_approval,
        requires_dual_approval=requires_dual_approval,
        requires_compliance_escalation=requires_compliance_escalation,
        reasons=reasons,
        redacted_output=redacted_output,
    )
