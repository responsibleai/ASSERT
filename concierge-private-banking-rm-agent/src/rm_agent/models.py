from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field


RiskTolerance = Literal["conservative", "moderate", "aggressive"]
Liquidity = Literal["liquid", "semi_liquid", "illiquid"]
WireStatus = Literal[
    "draft",
    "pending_rm_approval",
    "pending_dual_approval",
    "blocked",
    "approved",
]


class ToolResult(BaseModel):
    ok: bool = True
    message: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    blocked: bool = False
    blocked_reason: str | None = None
    required_checks_called: list[str] = Field(default_factory=list)


class ProposedAction(BaseModel):
    action_type: Literal["lookup", "email", "trade", "wire", "research", "security_check", "compliance_note", "general"]
    client_id: str | None = None
    target_client_id: str | None = None
    amount_usd: float | None = None
    ticker: str | None = None
    destination_country: str | None = None
    recipient_name: str | None = None
    recipient_domain: str | None = None
    uses_research_note_id: str | None = None
    email_subject: str | None = None
    email_body: str | None = None
    rationale: str | None = None
    required_checks: list[str] = Field(default_factory=list)
    approvals: list[str] = Field(default_factory=list)
    session_id: str


class PolicyDecision(BaseModel):
    allowed: bool
    blocked: bool
    requires_rm_approval: bool
    requires_dual_approval: bool
    requires_compliance_escalation: bool
    reasons: list[str] = Field(default_factory=list)
    redacted_output: str | None = None


class LookupClientInput(BaseModel):
    client_name_or_id: str


class LookupPortfolioInput(BaseModel):
    client_id: str


class LookupKYCInput(BaseModel):
    client_id: str


class RestrictedCheckInput(BaseModel):
    ticker: str


class SuitabilityCheckInput(BaseModel):
    client_id: str
    ticker_or_asset: str
    amount_usd: float = Field(gt=0)


class AMLCheckInput(BaseModel):
    client_id: str
    amount_usd: float = Field(gt=0)
    destination_country: str
    recipient_name: str


class SanctionsCheckInput(BaseModel):
    destination_country: str
    recipient_name: str


class DraftEmailInput(BaseModel):
    client_id: str
    subject: str
    body: str


class ProposeTradeInput(BaseModel):
    client_id: str
    ticker: str
    action: Literal["buy", "sell"]
    amount_usd: float = Field(gt=0)
    rationale: str


class InitiateWireInput(BaseModel):
    client_id: str
    amount_usd: float = Field(gt=0)
    destination_country: str
    recipient_name: str
    recipient_bank: str


class ApprovalInput(BaseModel):
    action_summary: str


class ResearchNoteInput(BaseModel):
    ticker: str


class EvalScenario(BaseModel):
    id: int
    name: str
    prompt: str
    category: Literal["safe", "unsafe"]
    expect_blocked: bool = False
    expect_approval: bool = False
    expect_pii_leak: bool = False
    expect_mnpi_leak: bool = False
    expect_client_conflation: bool = False
    expected_policy_reasons: list[str] = Field(default_factory=list)


class EvalResult(BaseModel):
    scenario_id: int
    name: str
    policy_pass: bool
    required_checks_called: list[str]
    blocked_when_required: bool
    approval_required: bool
    pii_leak: bool
    mnpi_leak: bool
    client_conflation: bool
    explanation_quality: int = Field(ge=1, le=5)
    final_score: Literal["pass", "fail"]
    response: str


class ClientSummary(BaseModel):
    client_id: str
    name: str
    email: str
    risk_tolerance: RiskTolerance
    accredited_investor: bool
    country: str
    tax_residency: str
    kyc_last_reviewed: date | str
    rm_id: str
