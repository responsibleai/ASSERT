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
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                title TEXT,
                sensitivity TEXT,
                content TEXT,
                jurisdictions TEXT,
                created_at TEXT
            )
            """
        )
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
    mcp = FastMCP("sqlite-mcp")

    @mcp.tool()
    async def datastore_save_record(
        document_id: str,
        title: str,
        sensitivity: str,
        content: str,
        jurisdictions: list[str],
    ) -> str:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO documents
                (id, title, sensitivity, content, jurisdictions, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    document_id,
                    title,
                    sensitivity,
                    content,
                    json.dumps(jurisdictions),
                    datetime.utcnow().isoformat(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return json.dumps({"status": "ok", "id": document_id})

    @mcp.tool()
    async def datastore_query(sql: str) -> str:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute(sql)
            if cursor.description is None:
                conn.commit()
                return json.dumps({"status": "ok", "rows": 0})
            rows = [dict(row) for row in cursor.fetchall()]
            return json.dumps({"status": "ok", "rows": rows})
        finally:
            conn.close()

    return mcp


def main() -> None:
    parser = argparse.ArgumentParser(description="SQLite MCP server for DLP demo")
    parser.add_argument("--db", required=True, help="Path to SQLite database file")
    args = parser.parse_args()

    init_db(args.db)
    mcp = make_server(args.db)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
