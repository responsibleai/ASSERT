from __future__ import annotations

import typer
from rich.console import Console

from .policies import detect_policy_refusal
from .tools import BankingTools


app = typer.Typer(help="Run optional MCP server for RM tools")
console = Console()


def _build_mcp_server():
    try:
        from mcp.server.fastmcp import FastMCP
    except Exception:
        return None

    tools = BankingTools()
    mcp = FastMCP("concierge-private-banking-rm-agent")

    @mcp.tool()
    def lookup_client(client_name_or_id: str) -> dict:
        return tools.lookup_client(client_name_or_id).model_dump()

    @mcp.tool()
    def lookup_portfolio(client_id: str) -> dict:
        return tools.lookup_portfolio(client_id).model_dump()

    @mcp.tool()
    def draft_client_email(client_id: str, subject: str, body: str) -> dict:
        return tools.draft_client_email(client_id, subject, body).model_dump()

    @mcp.tool()
    def initiate_wire_request(
        client_id: str,
        amount_usd: float,
        destination_country: str,
        recipient_name: str,
        recipient_bank: str,
        user_request_text: str = "",
    ) -> dict:
        refusal = detect_policy_refusal(user_request_text)
        if refusal:
            return {
                "ok": False,
                "blocked": True,
                "blocked_reason": refusal.reason,
                "message": refusal.refusal_message,
            }
        return tools.initiate_wire_request(
            client_id=client_id,
            amount_usd=amount_usd,
            destination_country=destination_country,
            recipient_name=recipient_name,
            recipient_bank=recipient_bank,
        ).model_dump()

    return mcp


@app.command()
def main() -> None:
    server = _build_mcp_server()
    if server is None:
        console.print("MCP SDK not installed. Install with: python -m pip install -e '.[mcp]'")
        raise typer.Exit(code=1)
    console.print("Starting MCP server for Concierge Private Banking RM Agent...")
    server.run()


if __name__ == "__main__":
    app()
