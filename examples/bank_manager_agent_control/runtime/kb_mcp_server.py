"""Knowledge-base MCP server — exposes Foundry IQ-style retrieval as a tool.

Single tool `knowledge_base_retrieve(query)` delegating to the backend chosen
by KB_BACKEND (mock BM25 now, real Foundry IQ at P5). Spawned as a SECOND MCP
server alongside the banking server (the agent concatenates both tool lists).

The returned JSON carries `grounded` + `citations` — typed signals the ACS
'ungrounded_policy_claim' feature gate consumes (vs. pattern-matching prose).
"""

import argparse
import json
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# Robust whether spawned as a script (`python kb_mcp_server.py`, sibling import)
# or as a module (`python -m examples...kb_mcp_server`, package import).
try:
    from kb_backend import get_backend
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).parent))
    from kb_backend import get_backend


def make_server() -> FastMCP:
    mcp = FastMCP("bank-knowledge-mcp")
    backend = get_backend()

    @mcp.tool()
    async def knowledge_base_retrieve(query: str) -> str:
        """Retrieve grounded answers from the bank policy/product knowledge base.

        Use this for policy and product questions (approval limits, LTV caps,
        margin-call policy, restricted-securities rules, KYC/AML procedure,
        fees, disputes). Returns a synthesized answer plus `citations`
        (ref_id + source + snippet) and a `grounded` boolean. If `grounded`
        is false, the corpus does not cover the question — do not fabricate a
        policy answer; route to verified bank operations.
        """
        return json.dumps(backend.retrieve(query))

    return mcp


def main() -> None:
    argparse.ArgumentParser(description="Bank knowledge-base MCP server").parse_args()
    make_server().run(transport="stdio")


if __name__ == "__main__":
    main()
