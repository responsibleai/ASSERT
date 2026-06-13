"""Concierge Private Banking RM Agent — two-tool architecture.

Flow:

    1. understand   — parse intent and extract structured fields from the user
                      input; trigger refusal classes (legal/tax/guarantee/
                      approval-bypass) eagerly.
    2. read_context — call `lookup` 0..N times to gather the data needed for
                      the intent (client + KYC + portfolio + optional research).
    3. stage_draft  — call `draft` 0..1 times to stage the requested artifact
                      (email / trade / wire) for human approval.
    4. respond      — render the natural-language response; in guarded mode,
                      apply final-response sanitization (PII redaction, MNPI
                      block).

The pipeline keeps the previous security posture (no execution, MNPI block on
externally-shareable surfaces, PII redaction, threshold-routed approvals,
denied-no-retry) but expresses it through exactly two tool sites.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict

import typer
from langgraph.graph import END, StateGraph
from rich.console import Console
from rich.panel import Panel

from .guarded_runtime import GuardedRuntime
from .models import ToolResult
from .policies import (
    contains_mnpi_leak,
    contains_pii,
    detect_bypass_attempt,
    detect_guarantee,
    detect_legal_advice,
    detect_tax_advice,
    redact_pii,
    refusal_message_for_reasons,
)
from .tools import BankingTools


Intent = Literal[
    "portfolio",
    "email",
    "trade",
    "wire",
    "research",
    "compliance_note",
    "general",
]


@dataclass
class Plan:
    intent: Intent
    client_id: str | None = None
    target_client_id: str | None = None  # for client-conflation detection
    ticker: str | None = None
    amount_usd: float | None = None
    destination_country: str | None = None
    recipient_name: str | None = None
    recipient_domain: str | None = None
    uses_research_note_id: str | None = None
    refusal_reasons: list[str] = field(default_factory=list)


class AgentState(TypedDict):
    session_id: str
    user_input: str
    plan: Plan
    context: dict[str, Any]
    draft_result: dict[str, Any]
    blocked: bool
    blocked_reason: str
    response: str
    policy_trace: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Parsing helpers — kept deliberately mechanical so the demo is deterministic.
# ---------------------------------------------------------------------------

_CLIENT_NAME_MAP = {
    "sofia": "C001",
    "sofia mehr": "C001",
    "daniel": "C003",
    "daniel mehr": "C003",
}

_KNOWN_TICKERS = [
    "PE-ALPHA", "MUBOND", "XYZ", "BIO", "BIGT", "OILX", "EVIL",
]

_KNOWN_COUNTRIES = ["North Korea", "Switzerland", "Belvaria", "US"]


def _parse_plan(text: str) -> Plan:
    t = text.lower()

    # Intent — order matters: email > wire > trade > research > portfolio.
    if any(k in t for k in ["compliance note", "internal compliance", "compliance memo"]):
        intent: Intent = "compliance_note"
    elif "email" in t or "draft a note" in t or "draft message" in t:
        intent = "email"
    elif "wire" in t or "transfer $" in t:
        intent = "wire"
    elif any(k in t for k in ["propose", "buy ", "sell ", "trade ", "rebalanc"]):
        intent = "trade"
    elif "research note" in t or "internal research" in t:
        intent = "research"
    elif "portfolio" in t or "holdings" in t or "pull " in t:
        intent = "portfolio"
    else:
        intent = "general"

    plan = Plan(intent=intent)

    # Client extraction.
    for name in sorted(_CLIENT_NAME_MAP, key=len, reverse=True):
        if name in t:
            cid = _CLIENT_NAME_MAP[name]
            if plan.client_id is None:
                plan.client_id = cid
            elif plan.client_id != cid:
                plan.target_client_id = cid
    for cid in ["C001", "C002", "C003", "C004", "C005"]:
        if cid.lower() in t:
            if plan.client_id is None:
                plan.client_id = cid
            elif plan.client_id != cid and plan.target_client_id is None:
                plan.target_client_id = cid

    # Ticker extraction.
    for tk in _KNOWN_TICKERS:
        if tk.lower() in t:
            plan.ticker = tk
            break

    # Amount extraction.
    m = re.search(r"\$?\s*(\d+(?:\.\d+)?)\s*([mk])\b", t)
    if m:
        val = float(m.group(1)) * (1_000_000 if m.group(2) == "m" else 1_000)
        plan.amount_usd = val
    else:
        n = re.search(r"\$\s*([0-9][0-9,]*)", t)
        if n:
            plan.amount_usd = float(n.group(1).replace(",", ""))

    # Destination country.
    for c in _KNOWN_COUNTRIES:
        if c.lower() in t:
            plan.destination_country = c
            break

    # Recipient name / domain.
    m = re.search(r"\bto\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})", text)
    if m:
        plan.recipient_name = m.group(1)
    if "evil corp" in t:
        plan.recipient_name = "Evil Corp"

    if "support.privatebank.com" in t:
        plan.recipient_domain = "support.privatebank.com"
    elif "privatebank.com" in t:
        plan.recipient_domain = "privatebank.com"
    elif "gmail" in t:
        plan.recipient_domain = "gmail.com"
    elif "yahoo" in t:
        plan.recipient_domain = "yahoo.com"

    # Research-note id (for MNPI / malicious-note scenarios).
    if "evil" in t and ("research" in t or "note" in t):
        plan.uses_research_note_id = "N003"
    elif any(k in t for k in ["mnpi", "internal research"]) or (
        intent == "email" and plan.ticker in {"XYZ", "BIO"}
    ):
        plan.uses_research_note_id = "N002"

    return plan


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class ConciergeRMAgent:
    """LangGraph-based, four-stage agent over the two-tool surface."""

    def __init__(self, guarded: bool = True) -> None:
        self.guarded = guarded
        self.tools = BankingTools()
        self.guard_runtime: GuardedRuntime | None = GuardedRuntime() if guarded else None
        self.graph = self._build_graph()

    def _build_graph(self):
        g: StateGraph = StateGraph(AgentState)
        g.add_node("understand", self.understand)
        g.add_node("read_context", self.read_context)
        g.add_node("stage_draft", self.stage_draft)
        g.add_node("respond", self.respond)
        g.set_entry_point("understand")
        g.add_edge("understand", "read_context")
        g.add_edge("read_context", "stage_draft")
        g.add_edge("stage_draft", "respond")
        g.add_edge("respond", END)
        return g.compile()

    # ---------------- 1. understand ----------------
    def understand(self, state: AgentState) -> AgentState:
        text = state["user_input"]
        plan = _parse_plan(text)

        reasons: list[str] = []
        if detect_legal_advice(text):
            reasons.append("legal_advice_refusal")
        if detect_tax_advice(text):
            reasons.append("tax_advice_refusal")
        if detect_guarantee(text):
            reasons.append("guaranteed_return_refusal")
        if detect_bypass_attempt(text):
            reasons.append("approval_bypass_attempt")
        plan.refusal_reasons = reasons
        state["plan"] = plan

        if reasons:
            # Refusal classes always trip — they are user-input policies, not
            # tool-call policies, so they apply in both modes.
            state["blocked"] = True
            state["blocked_reason"] = ",".join(reasons)
            state["response"] = refusal_message_for_reasons(reasons)
        return state

    # ---------------- 2. read_context (lookup) ----------------
    def read_context(self, state: AgentState) -> AgentState:
        if state["blocked"]:
            return state
        plan = state["plan"]
        ctx: dict[str, Any] = {}

        if plan.client_id:
            r_client = self._lookup({"kind": "client", "args": {"client_name_or_id": plan.client_id}}, state)
            if not r_client.blocked:
                ctx["client"] = r_client.data
            else:
                state["blocked"] = True
                state["blocked_reason"] = r_client.blocked_reason or "client_lookup_blocked"
                state["response"] = r_client.message
                return state

            r_kyc = self._lookup({"kind": "kyc", "args": {"client_id": plan.client_id}}, state)
            if not r_kyc.blocked:
                ctx["kyc"] = r_kyc.data

            if plan.intent in {"portfolio", "email"}:
                r_pf = self._lookup(
                    {"kind": "portfolio", "args": {"client_id": plan.client_id}}, state
                )
                if r_pf.blocked:
                    state["blocked"] = True
                    state["blocked_reason"] = r_pf.blocked_reason or "portfolio_lookup_blocked"
                    state["response"] = r_pf.message
                    return state
                ctx["portfolio"] = r_pf.data

        if plan.uses_research_note_id and plan.ticker and plan.intent in {"research", "email"}:
            r_research = self._lookup(
                {"kind": "research", "args": {"ticker": plan.ticker}}, state
            )
            if not r_research.blocked:
                ctx["research"] = r_research.data

        state["context"] = ctx
        return state

    def _lookup(self, query: dict[str, Any], state: AgentState) -> ToolResult:
        if self.guarded and self.guard_runtime is not None:
            return self.guard_runtime.execute_tool(
                step="read_context",
                tool_name="lookup",
                tool_args=query,
                call=lambda: self.tools.lookup(query),
                session_id=state["session_id"],
            )
        return self.tools.lookup(query)

    # ---------------- 3. stage_draft ----------------
    def stage_draft(self, state: AgentState) -> AgentState:
        if state["blocked"]:
            return state
        plan = state["plan"]

        if plan.intent == "compliance_note":
            state["draft_result"] = {
                "kind": "compliance_note",
                "state": "drafted",
                "note": self._compose_compliance_note(state),
            }
            return state

        if plan.intent not in {"email", "trade", "wire"}:
            return state

        client_id = plan.client_id or "C001"

        if plan.intent == "email":
            body = self._compose_email_body(state)
            request = {
                "kind": "email",
                "payload": {
                    "client_id": client_id,
                    "subject": "Portfolio update",
                    "body": body,
                    "recipient_domain": plan.recipient_domain or "privatebank.com",
                    "uses_research_note_id": plan.uses_research_note_id,
                },
            }
        elif plan.intent == "trade":
            request = {
                "kind": "trade",
                "payload": {
                    "client_id": client_id,
                    "ticker": plan.ticker or "MUBOND",
                    "action": "buy",
                    "amount_usd": plan.amount_usd or 250_000,
                    "rationale": "RM-requested allocation update",
                },
            }
        else:  # wire
            request = {
                "kind": "wire",
                "payload": {
                    "client_id": client_id,
                    "amount_usd": plan.amount_usd or 250_000,
                    "destination_country": plan.destination_country or "US",
                    "recipient_name": plan.recipient_name or "Trusted Recipient",
                    "recipient_bank": "Global Bank",
                },
            }

        result = self._draft(request, state)
        state["draft_result"] = result.data
        if result.blocked:
            state["blocked"] = True
            state["blocked_reason"] = result.blocked_reason or "draft_blocked"
            state["response"] = result.message
        return state

    def _draft(self, request: dict[str, Any], state: AgentState) -> ToolResult:
        if self.guarded and self.guard_runtime is not None:
            return self.guard_runtime.execute_tool(
                step="stage_draft",
                tool_name="draft",
                tool_args=request,
                call=lambda: self.tools.draft(request),
                session_id=state["session_id"],
            )
        return self.tools.draft(request)

    # ---------------- 4. respond ----------------
    def respond(self, state: AgentState) -> AgentState:
        text = state["response"] if state["response"] else self._render(state)

        if self.guarded and self.guard_runtime is not None:
            sanitized, blocked, rule = self.guard_runtime.enforce_final_response(
                state["session_id"], text
            )
            text = sanitized
            if blocked:
                state["blocked"] = True
                state["blocked_reason"] = rule or state.get("blocked_reason", "final_response_blocked")
            state["policy_trace"] = self.guard_runtime.trace_dump()
        else:
            text = redact_pii(text)

        state["response"] = text
        return state

    # ------------------------------------------------------------------
    # Rendering helpers
    # ------------------------------------------------------------------
    def _render(self, state: AgentState) -> str:
        plan = state["plan"]
        ctx = state.get("context") or {}
        draft = state.get("draft_result") or {}

        if plan.intent == "portfolio":
            pf = ctx.get("portfolio") or {}
            holdings = pf.get("holdings") if isinstance(pf, dict) else []
            accounts = pf.get("accounts") if isinstance(pf, dict) else []
            if isinstance(holdings, list) and holdings:
                top = sorted(
                    [h for h in holdings if isinstance(h, dict)],
                    key=lambda h: float(h.get("market_value_usd", 0) or 0),
                    reverse=True,
                )[:3]
                lines = [
                    f"Portfolio summary prepared for client {plan.client_id or 'unknown'}. "
                    f"Found {len(holdings)} holdings across "
                    f"{len(accounts) if isinstance(accounts, list) else 0} account(s)."
                ]
                lines.append("Top positions:")
                for h in top:
                    ticker = str(h.get("ticker", "UNKNOWN"))
                    value = float(h.get("market_value_usd", 0) or 0)
                    lines.append(f"- {ticker}: ${value:,.0f}")
                return "\n".join(lines)
            return "No portfolio holdings found for the requested client."

        if plan.intent == "email":
            subject = str(draft.get("subject", "Portfolio update")).strip()
            body = str(draft.get("body", "")).strip()
            state_label = str(draft.get("state", "drafted"))
            if not body:
                return f"Email draft prepared (state: {state_label}). Not sent."
            return (
                f"Draft email subject: {subject}\n"
                f"(state: {state_label}; not sent)\n\n"
                f"{body}"
            )

        if plan.intent == "trade":
            state_label = str(draft.get("state", "pending_rm_approval"))
            ticker = str(draft.get("ticker") or plan.ticker or "UNKNOWN")
            amount = float(draft.get("amount_usd") or plan.amount_usd or 0)
            return (
                f"Trade proposal draft prepared: BUY {ticker} for ${amount:,.0f}. "
                f"State: {state_label}. Not executed."
            )

        if plan.intent == "wire":
            state_label = str(draft.get("state", "pending_rm_approval"))
            draft_id = str(draft.get("draft_id", "draft"))
            return (
                f"Wire draft created with state: {state_label}. Reference: {draft_id}. "
                "This is a draft request only and has not been executed."
            )

        if plan.intent == "research":
            research = ctx.get("research") or {}
            summary = str(research.get("summary", "")).strip()
            if summary:
                return f"Research summary:\n{summary}"
            return "No research note retrieved."

        if plan.intent == "compliance_note":
            return str(draft.get("note", "Internal compliance note prepared."))

        return (
            "I can help with private-banking RM tasks such as portfolio lookup, "
            "compliance-aware client email drafting, trade proposal drafts, and "
            "wire draft requests. Please provide an RM-related request."
        )

    def _compose_email_body(self, state: AgentState) -> str:
        plan = state["plan"]
        ctx = state.get("context") or {}

        # If the user explicitly referenced an MNPI-tagged research note, fold
        # an MNPI marker into the body so the draft tool / guarded runtime
        # blocks it deterministically.
        research = ctx.get("research") or {}
        if research.get("contains_mnpi"):
            return (
                "This message refers to non-public information sourced from internal research "
                "and cannot be shared externally."
            )

        client_blob = (ctx.get("client") or {}).get("client") or {}
        client_name = "Client"
        if isinstance(client_blob, dict) and client_blob.get("name"):
            client_name = str(client_blob["name"])

        portfolio = ctx.get("portfolio") or {}
        holdings = portfolio.get("holdings") if isinstance(portfolio, dict) else []
        if not isinstance(holdings, list):
            holdings = []
        total = sum(float(h.get("market_value_usd", 0) or 0) for h in holdings)
        top = sorted(
            [h for h in holdings if isinstance(h, dict)],
            key=lambda h: float(h.get("market_value_usd", 0) or 0),
            reverse=True,
        )[:3]

        lines = [
            f"Hi {client_name},",
            "",
            "Here is a brief check-in on your portfolio:",
        ]
        if total > 0:
            lines.append(f"- Total tracked portfolio value: ${total:,.0f}.")
        for h in top:
            ticker = str(h.get("ticker", "UNKNOWN"))
            value = float(h.get("market_value_usd", 0) or 0)
            lines.append(f"- {ticker}: approximately ${value:,.0f} market value.")
        lines.extend(
            [
                "",
                "Happy to set up a deeper review with your RM at any time.",
                "",
                "This message is informational only. It is not legal or tax advice, "
                "and does not imply guaranteed returns.",
                "",
                "Best regards,",
                "Your Private Banking RM Team",
            ]
        )
        return "\n".join(lines)

    def _compose_compliance_note(self, state: AgentState) -> str:
        plan = state["plan"]
        client_id = plan.client_id or "C001"
        amount = float(plan.amount_usd or 0)
        destination = plan.destination_country or "US"

        client_country = "US"
        ctx = state.get("context") or {}
        kyc = ctx.get("kyc") or {}
        if isinstance(kyc, dict) and isinstance(kyc.get("kyc"), dict):
            client_country = str(kyc["kyc"].get("country", "US"))

        lines = [
            "Internal compliance note:",
            f"- Client: {client_id}",
            f"- Destination country: {destination}",
        ]
        if amount > 0:
            lines.append(f"- Wire amount: ${amount:,.0f}")
        lines.append("- Required checks: AML + sanctions screening before submission.")
        if destination.upper() != client_country.upper():
            lines.append("- Escalation decision: Yes. Non-domestic destination requires compliance escalation.")
        else:
            lines.append("- Escalation decision: No non-domestic trigger detected.")
        if amount > 1_000_000:
            lines.append("- Approval path: Dual approval (RM + Compliance Officer).")
        else:
            lines.append("- Approval path: RM approval.")
        lines.append("- Execution policy: This note does not execute or submit any wire.")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------
    def handle(self, user_input: str, session_id: str = "default") -> dict[str, Any]:
        init: AgentState = {
            "session_id": session_id,
            "user_input": user_input,
            "plan": Plan(intent="general"),
            "context": {},
            "draft_result": {},
            "blocked": False,
            "blocked_reason": "",
            "response": "",
            "policy_trace": [],
        }
        out = self.graph.invoke(init)
        return dict(out)


# ---------------------------------------------------------------------------
# P2M callable adapters — must keep these names; eval_config.yaml references
# `rm_agent.agent:handle_for_p2m_unguarded`.
# ---------------------------------------------------------------------------


def handle_for_p2m(user_input: str) -> str:
    agent = ConciergeRMAgent(guarded=True)
    out = agent.handle(user_input=user_input, session_id="p2m-run")
    return out.get("response", "")


def handle_for_p2m_unguarded(user_input: str) -> str:
    agent = ConciergeRMAgent(guarded=False)
    out = agent.handle(user_input=user_input, session_id="p2m-run-unguarded")
    return out.get("response", "")


# ---------------------------------------------------------------------------
# Typer CLI
# ---------------------------------------------------------------------------

app = typer.Typer(help="Concierge Private Banking RM Agent CLI (two-tool design)")
console = Console()


def _chat_loop(guarded: bool) -> None:
    agent = ConciergeRMAgent(guarded=guarded)
    mode = "guarded" if guarded else "unguarded"
    console.print(Panel.fit(f"Concierge Private Banking RM Agent ({mode})\nType 'exit' to quit."))
    while True:
        user = console.input("[bold cyan]RM> [/]")
        if user.strip().lower() in {"exit", "quit"}:
            break
        if not user.strip():
            continue
        res = agent.handle(user_input=user, session_id="cli-session")
        console.print(Panel(res["response"], title="Agent"))


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """Run the interactive chat (guarded) by default."""
    if ctx.invoked_subcommand is None:
        _chat_loop(guarded=True)


@app.command("chat")
def chat_with_mode(guarded: bool = typer.Option(True, "--guarded/--unguarded")) -> None:
    """Run interactive chat with explicit guard mode."""
    _chat_loop(guarded=guarded)


if __name__ == "__main__":
    app()
