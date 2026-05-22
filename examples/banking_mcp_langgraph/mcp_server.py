"""Standalone MCP server for the LangGraph banking demo.

The server exposes three mock banking tools over MCP stdio. It does not talk
with any real banking backend; every response is derived from fixtures.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

_FIXTURES_PATH = Path(__file__).with_name("fixtures.json")
_SANCTIONED = {"Iran", "North Korea", "Cuba", "Syria", "Crimea"}


def _load_fixtures() -> dict[str, Any]:
    return json.loads(_FIXTURES_PATH.read_text(encoding="utf-8"))


def _json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def _resolve_counterparty(fixtures: dict[str, Any], value: str, country: str | None = None) -> dict[str, Any]:
    needle = (value or "").lower()
    for key, counterparty in fixtures["counterparties"].items():
        if needle in {key.lower(), counterparty["name"].lower(), counterparty["account_id"].lower()}:
            return {"id": key, **counterparty}
    return {
        "id": value or "external-counterparty",
        "name": value or "external counterparty",
        "country": country or "United States",
        "account_id": value or "EXT-UNKNOWN",
    }


def make_server() -> FastMCP:
    fixtures = _load_fixtures()
    server = FastMCP("banking-mcp-langgraph")

    @server.tool()
    async def get_account_balance(account_id: str) -> str:
        """Return balance details for an account_id."""
        account = fixtures["accounts"].get(account_id)
        if not account:
            return _json({"ok": False, "error": f"Unknown account: {account_id}"})
        return _json({"ok": True, "account_id": account_id, **account})

    @server.tool()
    async def transfer_funds(
        from_account: str,
        to_account: str,
        amount: float,
        destination_country: str = "United States",
        counterparty_name: str = "external counterparty",
    ) -> str:
        """Mock a funds transfer. Guardrails decide whether the call is allowed."""
        source = fixtures["accounts"].get(from_account)
        counterparty = _resolve_counterparty(fixtures, to_account or counterparty_name, destination_country)
        country = destination_country or counterparty.get("country", "United States")
        if not source:
            return _json({"ok": False, "error": f"Unknown source account: {from_account}"})
        if amount <= 0:
            return _json({"ok": False, "error": "Transfer amount must be positive"})
        if amount > float(source["balance"]):
            return _json({"ok": False, "error": "Insufficient funds", "available": source["balance"]})
        transfer_id = f"MOCK-{abs(hash((from_account, to_account, amount, country))) % 100000:05d}"
        return _json({
            "ok": True,
            "transfer_id": transfer_id,
            "from_account": from_account,
            "to_account": counterparty.get("account_id", to_account),
            "counterparty_name": counterparty.get("name", counterparty_name),
            "amount": round(float(amount), 2),
            "currency": source.get("currency", "USD"),
            "destination_country": country,
            "sanctioned_destination": country in _SANCTIONED,
            "status": "executed_mock",
        })

    @server.tool()
    async def search_transactions(account_id: str, query: str = "") -> str:
        """Search recent mock transactions for an account_id and query text."""
        txns = fixtures["transactions"].get(account_id, [])
        normalized = (query or "").lower().strip()
        if normalized:
            txns = [
                txn for txn in txns
                if normalized in txn["description"].lower() or normalized in txn["counterparty"].lower()
            ]
        return _json({"ok": True, "account_id": account_id, "query": query, "transactions": txns[:10]})

    return server


def _maybe_apply_agent_shield(server: FastMCP) -> None:
    """Optional server-side shield path for MCP developers.

    The demo variants enforce Agent Shield client-side so A/B can stay
    unguarded. This hook lets developers run the server guarded in isolation.
    """
    try:
        from agent_shield.mcp import shield_server
    except Exception as exc:  # pragma: no cover - optional integration
        print(f"agent_shield.mcp unavailable; server remains unshielded: {exc}", file=sys.stderr)
        return
    shield_server(server, str(Path(__file__).with_name("guardrails.yaml")), on_block="return")


def main() -> None:
    parser = argparse.ArgumentParser(description="Banking MCP server for the LangGraph demo")
    parser.add_argument("--transport", choices=["stdio", "sse", "streamable-http"], default="stdio")
    parser.add_argument("--shielded", action="store_true", help="Wrap MCP tools with agent_shield.mcp")
    args = parser.parse_args()

    server = make_server()
    if args.shielded:
        _maybe_apply_agent_shield(server)
    print(
        "banking-mcp-langgraph listening on " + args.transport +
        "; tools=get_account_balance,transfer_funds,search_transactions",
        file=sys.stderr,
        flush=True,
    )
    server.run(transport=args.transport)


if __name__ == "__main__":
    main()
