"""Realistic multi-domain mock bank MCP server (feature-representation experiment).

THIN TRANSPORT WRAPPER. All entity data + tool logic live in `bank_core.py`
(no `mcp` dependency); this module only exposes those pure functions as MCP
tools and JSON-encodes their return dicts. There is exactly ONE copy of the
mock data — eliminating the "two diverging copies" confounder. Tests import
`bank_core` directly; the agent talks to this server.

The bank spans four domains, each tool surfacing *typed risk signals*:

  - Clients        (CL-####)  read_client
  - Deposit accts  (ACC-####) read_account, read_transaction_history,
                              prepare_transfer, request_customer_approval,
                              create_transfer, freeze_account, enable_admin_mode
  - Home loans     (LN-####)  read_loan, prepare_loan_modification
  - Brokerage      (BR-####)  read_portfolio, place_trade

DESIGN PRINCIPLE (the whole point of the experiment)
----------------------------------------------------
Every tool surfaces a normalized `risk_tier` (∈ {standard, high_net_worth,
vip, restricted}; sensitive == not "standard") plus domain-specific signals.
ACS guardrails gate on these typed signals, NOT on regex of the stringified
tool text. The original demo's gate was hardcoded `regex.match("ACC-100[23]")`
— it cannot protect a sensitive *loan* (LN-####) or *portfolio* (BR-####).
A feature gate on `risk_tier` covers all domains uniformly. The new ID
prefixes exist specifically to expose that generalization failure.

In-memory only; one process per agent turn (counters reset per turn via
bank_core.reset_state()).
"""

import argparse
import json

from mcp.server.fastmcp import FastMCP

# Robust whether spawned as a script (sibling import) or as a module.
try:
    import bank_core as core
except ImportError:  # pragma: no cover
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent))
    import bank_core as core

# Re-export the data tables so existing tests / smoke checks that reference
# `realistic_bank_mcp_server.ACCOUNTS` keep working against the single source.
CLIENTS = core.CLIENTS
ACCOUNTS = core.ACCOUNTS
TRANSACTIONS = core.TRANSACTIONS
LOANS = core.LOANS
PORTFOLIOS = core.PORTFOLIOS


def make_server() -> FastMCP:
    mcp = FastMCP("realistic-bank-mock-mcp")
    core.reset_state()

    # ----- Clients -------------------------------------------------------
    @mcp.tool()
    async def read_client(client_id: str) -> str:
        """Read KYC/relationship profile for a client.

        Returns typed risk signals: kyc_tier, risk_rating, pep_flag,
        sanctions_hit, relationship_manager, and a normalized risk_tier.
        Also returns entity_reads (cross-domain enumeration counter).
        """
        return json.dumps(core.read_client(client_id))

    # ----- Deposit accounts ---------------------------------------------
    @mcp.tool()
    async def read_account(account_id: str) -> str:
        """Read deposit-account details with typed risk signals.

        Includes `risk_tier` (normalized sensitivity), `account_sensitivity`,
        and `session_account_reads` (within-turn enumeration counter).
        """
        return json.dumps(core.read_account(account_id))

    @mcp.tool()
    async def read_transaction_history(account_id: str, limit: int = 10) -> str:
        """Read transactions; each row carries a typed `referenced_accounts` list.

        Top-level `referenced_accounts` aggregates entity IDs found in free-text
        descriptions (extracted in the data layer, across all domains).
        """
        return json.dumps(core.read_transaction_history(account_id, limit))

    @mcp.tool()
    async def prepare_transfer(from_account: str, to_account: str, amount: float, description: str = "") -> str:
        """Prepare (not execute) a transfer; returns typed fraud signals + risk_tier."""
        return json.dumps(core.prepare_transfer(from_account, to_account, amount, description))

    @mcp.tool()
    async def request_customer_approval(transfer_id: str) -> str:
        """Request customer approval for a pending transfer (auto-granted in demo)."""
        return json.dumps(core.request_customer_approval(transfer_id))

    @mcp.tool()
    async def create_transfer(transfer_id: str, customer_ack_token: str) -> str:
        """Execute an approved transfer (requires matching ack token)."""
        return json.dumps(core.create_transfer(transfer_id, customer_ack_token))

    @mcp.tool()
    async def freeze_account(account_id: str, reason: str) -> str:
        """Freeze an account (gated on admin_mode by ACS, not here)."""
        return json.dumps(core.freeze_account(account_id, reason))

    @mcp.tool()
    async def enable_admin_mode(reason: str) -> str:
        """Enable elevated admin mode (ACS denies user-initiated calls)."""
        return json.dumps(core.enable_admin_mode(reason))

    # ----- Home loans ----------------------------------------------------
    @mcp.tool()
    async def read_loan(loan_id: str) -> str:
        """Read a home/auto loan with typed risk signals.

        Signals: ltv, dti, delinquency_status, modification_flag,
        officer_authority_tier, and normalized risk_tier. (NEW DOMAIN —
        sensitive loans use the LN- prefix the original text gate can't match.)
        """
        return json.dumps(core.read_loan(loan_id))

    @mcp.tool()
    async def prepare_loan_modification(loan_id: str, new_rate: float = 0.0, forbearance_months: int = 0) -> str:
        """Prepare a loan modification (not execute). Returns required authority tier.

        A modification on a sensitive loan (executive/senior authority,
        delinquency, jumbo) is a high-risk action ACS should gate via the
        typed officer_authority_tier / risk_tier — never via a hardcoded ID.
        """
        return json.dumps(core.prepare_loan_modification(loan_id, new_rate, forbearance_months))

    # ----- Brokerage -----------------------------------------------------
    @mcp.tool()
    async def read_portfolio(portfolio_id: str) -> str:
        """Read a brokerage portfolio with typed risk signals.

        Signals: margin_level, position_concentration, pdt_flag,
        restricted_security, suitability_tier, and normalized risk_tier.
        (NEW DOMAIN — sensitive portfolios use the BR- prefix.)
        """
        return json.dumps(core.read_portfolio(portfolio_id))

    @mcp.tool()
    async def place_trade(portfolio_id: str, symbol: str, side: str, quantity: float) -> str:
        """Place a brokerage trade (not settled). Returns typed suitability/margin signals.

        A trade in a restricted security, on margin near a call, or
        exceeding suitability is high-risk — gate via the typed signals,
        not via the portfolio ID.
        """
        return json.dumps(core.place_trade(portfolio_id, symbol, side, quantity))

    return mcp


def main() -> None:
    argparse.ArgumentParser(description="Realistic multi-domain mock bank MCP server").parse_args()
    make_server().run(transport="stdio")


if __name__ == "__main__":
    main()
