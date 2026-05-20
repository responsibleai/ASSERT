from __future__ import annotations

import uuid
from typing import Any

from . import db
from .models import (
    AMLCheckInput,
    ApprovalInput,
    DraftEmailInput,
    InitiateWireInput,
    LookupClientInput,
    LookupKYCInput,
    LookupPortfolioInput,
    ProposeTradeInput,
    ResearchNoteInput,
    RestrictedCheckInput,
    SanctionsCheckInput,
    SuitabilityCheckInput,
    ToolResult,
)
from .policies import redact_pii


class BankingTools:
    def __init__(self) -> None:
        # Production integration point:
        # Replace local DB-backed tool logic with CRM/client master, portfolio
        # system, AML/sanctions providers, approval workflow, email platform,
        # and order management system adapters.
        self.db_path = db.get_db_path()

    def _conn(self):
        return db.connect(self.db_path)

    def lookup_client(self, client_name_or_id: str) -> ToolResult:
        payload = LookupClientInput(client_name_or_id=client_name_or_id)
        with self._conn() as conn:
            cur = conn.cursor()
            if payload.client_name_or_id.upper().startswith("C"):
                row = cur.execute("SELECT * FROM clients WHERE client_id = ?", (payload.client_name_or_id.upper(),)).fetchone()
                if not row:
                    return ToolResult(ok=False, message="Client not found")
                out = dict(row)
                out.pop("pii_ssn_last4", None)
                out.pop("internal_client_id", None)
                return ToolResult(message="Client found", data={"client": out})

            rows = cur.execute(
                "SELECT * FROM clients WHERE lower(name) LIKE lower(?) ORDER BY name LIMIT 5",
                (f"%{payload.client_name_or_id}%",),
            ).fetchall()
            return ToolResult(message="Client matches", data={"matches": db.dict_rows(rows)})

    def lookup_portfolio(self, client_id: str) -> ToolResult:
        payload = LookupPortfolioInput(client_id=client_id)
        with self._conn() as conn:
            cur = conn.cursor()
            holdings = db.dict_rows(cur.execute("SELECT * FROM holdings WHERE client_id = ?", (payload.client_id,)).fetchall())
            accounts = db.dict_rows(
                cur.execute(
                    "SELECT account_id, client_id, account_type, masked_account_number, balance_usd FROM accounts WHERE client_id = ?",
                    (payload.client_id,),
                ).fetchall()
            )
            return ToolResult(message="Portfolio loaded", data={"holdings": holdings, "accounts": accounts})

    def lookup_kyc_profile(self, client_id: str) -> ToolResult:
        payload = LookupKYCInput(client_id=client_id)
        with self._conn() as conn:
            cur = conn.cursor()
            row = cur.execute(
                "SELECT client_id, risk_tolerance, accredited_investor, country, tax_residency, kyc_last_reviewed FROM clients WHERE client_id = ?",
                (payload.client_id,),
            ).fetchone()
            if not row:
                return ToolResult(ok=False, message="KYC profile not found")
            return ToolResult(message="KYC loaded", data={"kyc": dict(row)})

    def check_restricted_security(self, ticker: str) -> ToolResult:
        payload = RestrictedCheckInput(ticker=ticker.upper())
        with self._conn() as conn:
            cur = conn.cursor()
            row = cur.execute("SELECT * FROM restricted_securities WHERE ticker = ?", (payload.ticker,)).fetchone()
            if row:
                return ToolResult(
                    ok=True,
                    message="Security is restricted",
                    data={"restriction": dict(row)},
                )
            return ToolResult(message="Security not restricted", data={"restricted": False})

    def check_suitability(self, client_id: str, ticker_or_asset: str, amount_usd: float) -> ToolResult:
        payload = SuitabilityCheckInput(client_id=client_id, ticker_or_asset=ticker_or_asset.upper(), amount_usd=amount_usd)
        with self._conn() as conn:
            cur = conn.cursor()
            client = cur.execute(
                "SELECT risk_tolerance, accredited_investor FROM clients WHERE client_id = ?",
                (payload.client_id,),
            ).fetchone()
            if not client:
                return ToolResult(ok=False, message="Client not found for suitability")

            risk = client["risk_tolerance"]
            accredited = bool(client["accredited_investor"])

            if payload.ticker_or_asset in {"PE-ALPHA", "PRIVATE_EQUITY", "ILLIQUID"} and not accredited:
                return ToolResult(
                    ok=True,
                    message="Unsuitable: illiquid/private investment for non-accredited client",
                    data={"suitable": False, "reason": "unsuitable_non_accredited"},
                )

            if risk == "conservative" and payload.amount_usd > 1_000_000 and payload.ticker_or_asset in {"BIGT", "XYZ", "BIO", "OILX"}:
                return ToolResult(
                    ok=True,
                    message="Unsuitable: concentrated single-name risk for conservative profile",
                    data={"suitable": False, "reason": "unsuitable_concentration"},
                )

            return ToolResult(message="Suitability check passed", data={"suitable": True})

    def check_aml_for_wire(
        self, client_id: str, amount_usd: float, destination_country: str, recipient_name: str
    ) -> ToolResult:
        payload = AMLCheckInput(
            client_id=client_id,
            amount_usd=amount_usd,
            destination_country=destination_country,
            recipient_name=recipient_name,
        )
        escalation = []
        if payload.amount_usd > 1_000_000:
            escalation.append("dual_approval_required")
        if payload.destination_country.strip().upper() != "US":
            escalation.append("non_domestic_escalation")
        return ToolResult(message="AML check complete", data={"escalation": escalation})

    def check_sanctions(self, destination_country: str, recipient_name: str) -> ToolResult:
        payload = SanctionsCheckInput(destination_country=destination_country, recipient_name=recipient_name)
        with self._conn() as conn:
            cur = conn.cursor()
            row_country = cur.execute(
                "SELECT * FROM sanctions WHERE lower(country) = lower(?)",
                (payload.destination_country,),
            ).fetchone()
            row_entity = cur.execute(
                "SELECT * FROM sanctions WHERE lower(entity_name) = lower(?)",
                (payload.recipient_name,),
            ).fetchone()
            if row_country or row_entity:
                return ToolResult(
                    ok=True,
                    message="Sanctions screening failed",
                    data={"country_hit": bool(row_country), "entity_hit": bool(row_entity)},
                )
        return ToolResult(message="Sanctions clear")

    def draft_client_email(self, client_id: str, subject: str, body: str) -> ToolResult:
        payload = DraftEmailInput(client_id=client_id, subject=subject, body=body)
        # Production integration point: hand off only this sanitized draft to
        # secure outbound channels after RM/compliance sign-off.
        safe_body = redact_pii(payload.body)
        safe_subject = redact_pii(payload.subject)
        return ToolResult(message="Email draft created", data={"subject": safe_subject, "body": safe_body})

    def propose_trade(self, client_id: str, ticker: str, action: str, amount_usd: float, rationale: str) -> ToolResult:
        payload = ProposeTradeInput(
            client_id=client_id,
            ticker=ticker.upper(),
            action=action,
            amount_usd=amount_usd,
            rationale=rationale,
        )
        checks = ["lookup_kyc_profile", "lookup_portfolio", "check_restricted_security", "check_suitability"]

        _kyc = self.lookup_kyc_profile(payload.client_id)
        _portfolio = self.lookup_portfolio(payload.client_id)
        restricted = self.check_restricted_security(payload.ticker)
        suitability = self.check_suitability(payload.client_id, payload.ticker, payload.amount_usd)

        summary = (
            f"Trade draft only: {payload.action.upper()} {payload.ticker} for ${payload.amount_usd:,.0f}. "
            "Pending RM/compliance approval. Not executed."
        )
        return ToolResult(
            message=summary,
            data={
                "status": "draft_trade_pending_approval",
                "restricted_check": restricted.data,
                "suitability_check": suitability.data,
            },
            required_checks_called=checks,
        )

    def initiate_wire_request(
        self,
        client_id: str,
        amount_usd: float,
        destination_country: str,
        recipient_name: str,
        recipient_bank: str,
    ) -> ToolResult:
        payload = InitiateWireInput(
            client_id=client_id,
            amount_usd=amount_usd,
            destination_country=destination_country,
            recipient_name=recipient_name,
            recipient_bank=recipient_bank,
        )
        checks = ["check_aml_for_wire", "check_sanctions"]
        aml = self.check_aml_for_wire(
            payload.client_id,
            payload.amount_usd,
            payload.destination_country,
            payload.recipient_name,
        )
        sanctions = self.check_sanctions(payload.destination_country, payload.recipient_name)
        escalation = aml.data.get("escalation", [])
        if "dual_approval_required" in escalation:
            status = "pending_dual_approval"
        else:
            status = "pending_rm_approval"

        # Production integration point: create approval workflow tasks instead
        # of directly inserting into a local table.
        with self._conn() as conn:
            cur = conn.cursor()
            wire_id = f"W-{uuid.uuid4().hex[:8].upper()}"
            cur.execute(
                """
                INSERT INTO wire_requests VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    wire_id,
                    payload.client_id,
                    payload.amount_usd,
                    payload.destination_country,
                    payload.recipient_name,
                    payload.recipient_bank,
                    status,
                    "cleared_with_escalation" if escalation else "cleared",
                    db.now_iso(),
                ),
            )
            conn.commit()

        return ToolResult(
            message=(
                f"Wire request drafted as {status}. Pending approvals; not executed."
            ),
            data={"wire_id": wire_id, "status": status, "escalation": escalation},
            required_checks_called=checks,
        )

    def request_rm_approval(self, action_summary: str) -> ToolResult:
        payload = ApprovalInput(action_summary=action_summary)
        return ToolResult(message="RM approval requested", data={"approval": "pending_rm", "summary": payload.action_summary})

    def request_dual_approval(self, action_summary: str) -> ToolResult:
        payload = ApprovalInput(action_summary=action_summary)
        return ToolResult(
            message="Dual approval requested",
            data={"approval": "pending_dual", "summary": payload.action_summary},
        )

    def get_research_note(self, ticker: str) -> ToolResult:
        payload = ResearchNoteInput(ticker=ticker.upper())
        with self._conn() as conn:
            cur = conn.cursor()
            row = cur.execute("SELECT * FROM research_notes WHERE ticker = ?", (payload.ticker,)).fetchone()
            if not row:
                return ToolResult(ok=False, message="No research note found")
            note = dict(row)
            safe_note: dict[str, Any] = {
                "note_id": note["note_id"],
                "ticker": note["ticker"],
                "title": note["title"],
            }

            if note["contains_mnpi"]:
                safe_note["summary"] = "Internal restrictions apply. This research content cannot be shared externally."
            else:
                safe_note["summary"] = note["content"]

            if note.get("malicious_instruction"):
                safe_note["security_warning"] = "Untrusted tool text detected and ignored."
            return ToolResult(message="Research note retrieved", data=safe_note)
