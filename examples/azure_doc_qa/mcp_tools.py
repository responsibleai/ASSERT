"""Real MCP client setup for Foundry IQ + Microsoft Learn MCP servers.

Used by ProductDocsAgent in real (non-mock) mode. Requires:
- FOUNDRY_IQ_MCP_ENDPOINT: SSE endpoint for Foundry IQ knowledge base
- FOUNDRY_IQ_TOKEN: Bearer token for Foundry IQ authentication
- Node.js (npx): for Microsoft Learn MCP via stdio transport
"""

import os

from langchain_mcp_adapters.client import MultiServerMCPClient


async def create_mcp_client() -> MultiServerMCPClient:
    """Create MCP client connecting to Foundry IQ + Microsoft Learn MCP."""
    client = MultiServerMCPClient(
        {
            "foundry-iq": {
                "url": os.environ["FOUNDRY_IQ_MCP_ENDPOINT"],
                "transport": "sse",
                "headers": {
                    "Authorization": f"Bearer {os.environ['FOUNDRY_IQ_TOKEN']}",
                },
            },
            "microsoft-docs": {
                "command": "npx",
                "args": ["-y", "@microsoftdocs/mcp"],
                "transport": "stdio",
            },
        }
    )
    return client
