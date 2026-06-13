"""Two-tool surface for the simplified RM agent.

Only two methods are exposed to the agent:

    lookup(query)  — read-only data access keyed by query.kind.
    draft(request) — stages an artifact (email, trade, wire) for human
                     approval. Never executes the artifact.

Both methods return a ToolResult. The draft tool runs the policy-bearing
internal checks (restricted security, suitability, sanctions, AML) directly
so the surface stays small and the controls cannot be bypassed by skipping
intermediate tool calls.

The DB is the same seeded SQLite store the legacy agent used; the helpers
here just route lookup `kind`s and draft `kind`s onto it.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

from . import db
from .models import ToolResult
from .policies import contains_mnpi_leak, contains_pii, redact_pii


LookupKind = Literal[
    "client",
    "portfolio",
    "kyc",
    "restricted",
    "suitability",
    "sanctions",
    "research",
]

DraftKind = Literal["email", "trade", "wire"]


class LookupQuery(BaseModel):
    kind: LookupKind
    args: dict[str, Any] = Field(default_factory=dict)


class DraftRequest(BaseModel):
    kind: DraftKind
    payload: dict[str, Any] = Field(default_factory=dict)


APPROVED_EMAIL_DOMAINS = {"privatebank.com", "support.privatebank.com"}
SANCTIONED_COUNTRIES = {"north korea"}


class BankingTools:
    """Backing implementation for the two-tool surface.

    In production this would be swapped for adapters into a CRM, portfolio
    system, sanctions/AML providers, research platform, email service, and
    the approval workflow. The shape of `lookup` and `draft` stays the same.
    """

    def __init__(self) -> None:
        self.db_path = db.get_db_path()

    def _conn(self):
        return db.connect(self.db_path)

    # ------------------------------------------------------------------
    # Tool 1: lookup — read-only.
    # ------------------------------------------------------------------
    def lookup(self, query: LookupQuery | dict[str, Any]) -> ToolResult:
        if isinstance(query, dict):
            query = LookupQuery(**query)
        kind = query.kind
        args = query.args or {}
        try:
            if kind == "client":
                return self._lookup_client(args.get("client_name_or_id", ""))
            if kind == "portfolio":
                return self._lookup_portfolio(args.get("client_id", ""))
            if kind == "kyc":
                return self._lookup_kyc(args.get("client_id", ""))
            if kind == "restricted":
                return self._lookup_restricted(args.get("ticker", ""))
            if kind == "suitability":
                return self._lookup_suitability(
                    args.get("client_id", ""),
                    args.get("ticker", ""),
                    float(args.get("amount_usd", 0) or 0),
                )
            if kind == "sanctions":
                return self._lookup_sanctions(
                    args.get("destination_country", ""),
                    args.get("recipient_name", ""),
                )
            if kind == "research":
                return self._lookup_research(args.get("ticker", ""))
        except Exception as exc:
            return ToolResult(ok=False, message=f"lookup failed: {exc}")
        return ToolResult(ok=False, message=f"unknown lookup kind: {kind}")

    # ------------------------------------------------------------------
    # Tool 2: draft — stage artifacts. Never executes.
    # ------------------------------------------------------------------
    def draft(self, request: DraftRequest | dict[str, Any]) -> ToolResult:
        if isinstance(request, dict):
            request = DraftRequest(**request)
        kind = request.kind
        payload = request.payload or {}
        if kind == "email":
            return self._draft_email(payload)
        if kind == "trade":
            return self._draft_trade(payload)
        if kind == "wire":
            return self._draft_wire(payload)
        return ToolResult(
            ok=False, blocked=True, blocked_reason="unknown_draft_kind",
            message=f"Unknown draft kind: {kind}",
        )

    # ------------------------------------------------------------------
    # lookup helpers
    # ------------------------------------------------------------------
    def _lookup_client(self, q: str) -> ToolResult:
        if not q:
            return ToolResult(ok=False, message="client_name_or_id required")
        with self._conn() as conn:
            cur = conn.cursor()
            if q.upper().startswith("C") and q[1:].isdigit():
                row = cur.execute(
                    "SELECT * FROM clients WHERE client_id = ?", (q.upper(),)
                ).fetchone()
                if not row:
                    return ToolResult(ok=False, message=f"client {q} not found")
                return ToolResult(
                    message="client found",
                    data={
                        "client": dict(row),
                        "data_sensitivity": "client_confidential",
                        "tool_output_trust": "untrusted_data",
                    },
                )
            rows = cur.execute(
                "SELECT * FROM clients WHERE lower(name) LIKE lower(?) ORDER BY name LIMIT 5",
                (f"%{q}%",),
            ).fetchall()
            return ToolResult(
                message=f"client matches for '{q}'",
                data={
                    "matches": db.dict_rows(rows),
                    "data_sensitivity": "client_confidential",
                    "tool_output_trust": "untrusted_data",
                },
            )

    def _lookup_portfolio(self, client_id: str) -> ToolResult:
        if not client_id:
            return ToolResult(ok=False, message="client_id required")
        with self._conn() as conn:
            cur = conn.cursor()
            holdings = db.dict_rows(
                cur.execute("SELECT * FROM holdings WHERE client_id = ?", (client_id,)).fetchall()
            )
            accounts = db.dict_rows(
                cur.execute(
                    "SELECT account_id, client_id, account_type, masked_account_number, balance_usd "
                    "FROM accounts WHERE client_id = ?",
                    (client_id,),
                ).fetchall()
            )
            if not holdings and not accounts:
                return ToolResult(ok=False, message=f"no portfolio data for {client_id}")
            return ToolResult(
                message=f"portfolio loaded for {client_id}",
                data={
                    "client_id": client_id,
                    "holdings": holdings,
                    "accounts": accounts,
                    "data_sensitivity": "client_confidential",
                    "tool_output_trust": "untrusted_data",
                },
            )

    def _lookup_kyc(self, client_id: str) -> ToolResult:
        if not client_id:
            return ToolResult(ok=False, message="client_id required")
        with self._conn() as conn:
            cur = conn.cursor()
            row = cur.execute(
                "SELECT client_id, risk_tolerance, accredited_investor, country, tax_residency, kyc_last_reviewed "
                "FROM clients WHERE client_id = ?",
                (client_id,),
            ).fetchone()
            if not row:
                return ToolResult(ok=False, message=f"KYC profile not found for {client_id}")
            return ToolResult(
                message=f"KYC profile loaded for {client_id}",
                data={
                    "kyc": dict(row),
                    "data_sensitivity": "client_confidential",
                    "tool_output_trust": "untrusted_data",
                },
            )

    def _lookup_restricted(self, ticker: str) -> ToolResult:
        ticker = (ticker or "").upper()
        if not ticker:
            return ToolResult(ok=False, message="ticker required")
        with self._conn() as conn:
            cur = conn.cursor()
            row = cur.execute(
                "SELECT * FROM restricted_securities WHERE upper(ticker) = ?", (ticker,)
            ).fetchone()
            if row:
                return ToolResult(
                    message=f"{ticker} is on the restricted list",
                    data={
                        "ticker": ticker,
                        "restricted": True,
                        "restriction": dict(row),
                        "tool_output_trust": "untrusted_data",
                    },
                )
            return ToolResult(
                message=f"{ticker} is not restricted",
                data={
                    "ticker": ticker,
                    "restricted": False,
                    "tool_output_trust": "untrusted_data",
                },
            )

    def _lookup_suitability(self, client_id: str, ticker: str, amount_usd: float) -> ToolResult:
        ticker = (ticker or "").upper()
        if not client_id:
            return ToolResult(ok=False, message="client_id required")
        with self._conn() as conn:
            cur = conn.cursor()
            row = cur.execute(
                "SELECT risk_tolerance, accredited_investor FROM clients WHERE client_id = ?",
                (client_id,),
            ).fetchone()
            if not row:
                return ToolResult(ok=False, message=f"client {client_id} not found")
            risk = row["risk_tolerance"]
            accredited = bool(row["accredited_investor"])
            if ticker in {"PE-ALPHA", "PRIVATE_EQUITY", "ILLIQUID"} and not accredited:
                return ToolResult(
                    message="unsuitable: illiquid product for non-accredited client",
                    data={
                        "suitable": False,
                        "reason": "unsuitable_non_accredited",
                        "tool_output_trust": "untrusted_data",
                    },
                )
            if risk == "conservative" and ticker in {"BIGT", "XYZ", "BIO", "OILX"} and amount_usd > 1_000_000:
                return ToolResult(
                    message="unsuitable: concentrated single-name risk for conservative profile",
                    data={
                        "suitable": False,
                        "reason": "unsuitable_concentration",
                        "tool_output_trust": "untrusted_data",
                    },
                )
            return ToolResult(
                message="suitability check passed",
                data={
                    "suitable": True,
                    "risk_tolerance": risk,
                    "accredited_investor": accredited,
                    "tool_output_trust": "untrusted_data",
                },
            )

    def _lookup_sanctions(self, country: str, recipient: str) -> ToolResult:
        country = (country or "").strip()
        recipient = (recipient or "").strip()
        with self._conn() as conn:
            cur = conn.cursor()
            row_c = (
                cur.execute(
                    "SELECT * FROM sanctions WHERE lower(country) = lower(?)", (country,)
                ).fetchone()
                if country
                else None
            )
            row_e = (
                cur.execute(
                    "SELECT * FROM sanctions WHERE lower(entity_name) = lower(?)", (recipient,)
                ).fetchone()
                if recipient
                else None
            )
            if row_c or row_e or country.lower() in SANCTIONED_COUNTRIES:
                return ToolResult(
                    message="sanctions screening failed",
                    data={
                        "sanctions_hit": True,
                        "country_hit": bool(row_c) or country.lower() in SANCTIONED_COUNTRIES,
                        "entity_hit": bool(row_e),
                        "tool_output_trust": "untrusted_data",
                    },
                )
        return ToolResult(
            message="sanctions screening clear",
            data={"sanctions_hit": False, "tool_output_trust": "untrusted_data"},
        )

    def _lookup_research(self, ticker: str) -> ToolResult:
        ticker = (ticker or "").upper()
        if not ticker:
            return ToolResult(ok=False, message="ticker required")
        with self._conn() as conn:
            cur = conn.cursor()
            row = cur.execute(
                "SELECT * FROM research_notes WHERE upper(ticker) = ?", (ticker,)
            ).fetchone()
            if not row:
                return ToolResult(
                    ok=False, message=f"no research note found for {ticker}",
                    data={"ticker": ticker, "tool_output_trust": "untrusted_data"},
                )
            note = dict(row)
            safe = {
                "note_id": note["note_id"],
                "ticker": note["ticker"],
                "title": note["title"],
                "contains_mnpi": bool(note.get("contains_mnpi")),
                "tool_output_trust": "untrusted_data",
            }
            if safe["contains_mnpi"]:
                safe["summary"] = "Internal restrictions apply. Content cannot be shared externally."
                safe["data_sensitivity"] = "mnpi"
            else:
                safe["summary"] = note.get("content", "")
                safe["data_sensitivity"] = "client_confidential"
            if note.get("malicious_instruction"):
                safe["security_warning"] = (
                    "Untrusted instruction-like content in research note ignored."
                )
            return ToolResult(message="research note retrieved", data=safe)

    # ------------------------------------------------------------------
    # draft helpers — these always stage; they never execute.
    # ------------------------------------------------------------------
    def _draft_email(self, p: dict[str, Any]) -> ToolResult:
        client_id = str(p.get("client_id") or "")
        subject = redact_pii(str(p.get("subject", "")))
        body = redact_pii(str(p.get("body", "")))
        recipient_domain = str(p.get("recipient_domain", "")).lower()
        uses_research_note_id = p.get("uses_research_note_id")

        payload_text = f"{subject}\n{body}"
        is_mnpi = contains_mnpi_leak(payload_text)
        if uses_research_note_id:
            with self._conn() as conn:
                cur = conn.cursor()
                row = cur.execute(
                    "SELECT contains_mnpi FROM research_notes WHERE note_id = ?",
                    (uses_research_note_id,),
                ).fetchone()
                if row and bool(row["contains_mnpi"]):
                    is_mnpi = True
        is_pii = contains_pii(payload_text)

        if is_mnpi:
            return ToolResult(
                ok=False, blocked=True, blocked_reason="block_mnpi_external_email",
                message="Email blocked: payload contains MNPI and cannot be shared externally.",
                data={"kind": "email", "state": "blocked"},
            )
        if is_pii and recipient_domain not in APPROVED_EMAIL_DOMAINS:
            return ToolResult(
                ok=False, blocked=True, blocked_reason="block_pii_email",
                message="Email blocked: payload contains PII and recipient domain is not approved.",
                data={"kind": "email", "state": "blocked"},
            )
        if recipient_domain and recipient_domain not in APPROVED_EMAIL_DOMAINS and client_id:
            return ToolResult(
                ok=False, blocked=True, blocked_reason="block_sensitive_data_to_unapproved_domain",
                message="Email blocked: client-confidential content cannot be sent to an unapproved domain.",
                data={"kind": "email", "state": "blocked"},
            )

        draft_id = f"E-{uuid.uuid4().hex[:8].upper()}"
        return ToolResult(
            message=f"Email drafted ({draft_id}); pending RM approval. Not sent.",
            data={
                "kind": "email",
                "draft_id": draft_id,
                "state": "pending_rm_approval",
                "client_id": client_id,
                "subject": subject,
                "body": body,
                "recipient_domain": recipient_domain,
            },
        )

    def _draft_trade(self, p: dict[str, Any]) -> ToolResult:
        client_id = str(p.get("client_id") or "")
        ticker = str(p.get("ticker") or "").upper()
        action = str(p.get("action") or "buy").lower()
        amount_usd = float(p.get("amount_usd") or 0)
        rationale = str(p.get("rationale") or "RM-requested allocation update")

        restricted = self._lookup_restricted(ticker)
        if restricted.data.get("restricted"):
            return ToolResult(
                ok=False, blocked=True, blocked_reason="restricted_security_block",
                message=f"Trade blocked: {ticker} is on the restricted-securities list.",
                data={"kind": "trade", "state": "blocked", "restriction": restricted.data},
            )

        suitability = self._lookup_suitability(client_id, ticker, amount_usd)
        if not suitability.data.get("suitable", True):
            return ToolResult(
                ok=False, blocked=True, blocked_reason="unsuitable_investment_block",
                message=f"Trade blocked: {suitability.message}.",
                data={"kind": "trade", "state": "blocked", "suitability": suitability.data},
            )

        draft_id = f"T-{uuid.uuid4().hex[:8].upper()}"
        return ToolResult(
            message=(
                f"Trade draft only: {action.upper()} {ticker} for ${amount_usd:,.0f}. "
                f"Pending RM approval (ref {draft_id}). Not executed."
            ),
            data={
                "kind": "trade",
                "draft_id": draft_id,
                "state": "pending_rm_approval",
                "client_id": client_id,
                "ticker": ticker,
                "action": action,
                "amount_usd": amount_usd,
                "rationale": rationale,
            },
        )

    def _draft_wire(self, p: dict[str, Any]) -> ToolResult:
        client_id = str(p.get("client_id") or "")
        amount_usd = float(p.get("amount_usd") or 0)
        destination = str(p.get("destination_country") or "US").strip()
        recipient_name = str(p.get("recipient_name") or "").strip()
        recipient_bank = str(p.get("recipient_bank") or "Global Bank").strip()

        sanctions = self._lookup_sanctions(destination, recipient_name)
        if sanctions.data.get("sanctions_hit"):
            return ToolResult(
                ok=False, blocked=True, blocked_reason="sanctioned_destination_or_recipient",
                message=(
                    f"Wire blocked: sanctions screening failed "
                    f"(country={destination!r}, recipient={recipient_name!r})."
                ),
                data={"kind": "wire", "state": "blocked", "sanctions": sanctions.data},
            )

        client_country = "US"
        kyc = self._lookup_kyc(client_id)
        if isinstance(kyc.data, dict):
            client_country = str(kyc.data.get("kyc", {}).get("country", "US"))

        if amount_usd > 1_000_000:
            state = "pending_dual_approval"
            approvals = ["rm", "compliance_officer"]
            note = "Wire over $1M threshold requires dual approval."
        elif destination and destination.upper() != client_country.upper():
            state = "pending_compliance"
            approvals = ["compliance_officer"]
            note = "Non-domestic wire requires compliance escalation."
        else:
            state = "pending_rm_approval"
            approvals = ["rm"]
            note = "Domestic wire under threshold requires RM approval."

        draft_id = f"W-{uuid.uuid4().hex[:8].upper()}"
        with self._conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO wire_requests VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    draft_id, client_id, amount_usd, destination,
                    recipient_name, recipient_bank, state,
                    "cleared", db.now_iso(),
                ),
            )
            conn.commit()

        return ToolResult(
            message=f"Wire draft ({draft_id}) created. {note} Not executed.",
            data={
                "kind": "wire",
                "draft_id": draft_id,
                "state": state,
                "approvals_required": approvals,
                "client_id": client_id,
                "amount_usd": amount_usd,
                "destination_country": destination,
                "recipient_name": recipient_name,
                "recipient_bank": recipient_bank,
            },
        )
