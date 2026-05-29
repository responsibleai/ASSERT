import argparse
import json
import sqlite3
from datetime import datetime

from mcp.server.fastmcp import FastMCP


def init_db(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                to_address TEXT,
                subject TEXT,
                body TEXT,
                created_at TEXT
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def make_server(db_path: str) -> FastMCP:
    mcp = FastMCP("email-mcp")

    @mcp.tool()
    async def datastore_send_email(to: str, subject: str, body: str) -> str:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                """
                INSERT INTO outbox (to_address, subject, body, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (to, subject, body, datetime.utcnow().isoformat()),
            )
            conn.commit()
        finally:
            conn.close()
        return json.dumps({"status": "queued", "to": to})

    return mcp


def main() -> None:
    parser = argparse.ArgumentParser(description="Email MCP server (simulated)")
    parser.add_argument("--db", required=True, help="Path to SQLite database file")
    args = parser.parse_args()

    init_db(args.db)
    mcp = make_server(args.db)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
