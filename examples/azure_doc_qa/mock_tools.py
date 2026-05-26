"""Mock tool implementations for offline / CI use.

Provides mock versions of all tools used by the azure_doc_qa agents:
- Internal docs tools (always mocked — search + full retrieval)
- External/public docs tools (mock replacements for real MCP tools)
- Escalation tool (shared between agents)
"""

import json
from pathlib import Path

from langchain_core.tools import tool as lc_tool

INTERNAL_DOCS_DIR = Path(__file__).parent / "docs" / "internal"
EXTERNAL_DOCS_DIR = Path(__file__).parent / "docs" / "external"


# ---------------------------------------------------------------------------
# Internal docs tools (InternalDocsAgent — always mocked)
# ---------------------------------------------------------------------------


@lc_tool
def search_internal_docs(query: str, top_k: int = 3) -> str:
    """Search internal engineering documents.

    Args:
        query: Natural language search query.
        top_k: Maximum results to return.

    Returns:
        JSON list of matching document summaries.
    """
    results = []
    for doc_file in sorted(INTERNAL_DOCS_DIR.glob("*.md")):
        content = doc_file.read_text()
        if any(word.lower() in content.lower() for word in query.split()):
            results.append(
                {
                    "doc_id": doc_file.stem,
                    "title": content.split("\n")[0].lstrip("# "),
                    "snippet": content[:200],
                    "authority": "internal",
                }
            )
    return json.dumps(results[:top_k])


@lc_tool
def get_internal_document(doc_id: str) -> str:
    """Retrieve the full text of an internal engineering document.

    Args:
        doc_id: The document identifier (filename without extension).

    Returns:
        Full document content with metadata.
    """
    doc_path = INTERNAL_DOCS_DIR / f"{doc_id}.md"
    if not doc_path.exists():
        return json.dumps({"error": f"Document {doc_id} not found"})
    content = doc_path.read_text()
    return json.dumps(
        {
            "doc_id": doc_id,
            "title": content.split("\n")[0].lstrip("# "),
            "content": content,
            "authority": "internal",
        }
    )


# ---------------------------------------------------------------------------
# Escalation tool (shared)
# ---------------------------------------------------------------------------


@lc_tool
def escalate_to_human(reason: str, priority: str = "normal") -> str:
    """Escalate the conversation to a human support agent.

    Args:
        reason: Why the query cannot be handled by the AI system.
        priority: "normal", "high", or "urgent".

    Returns:
        Ticket confirmation with ticket_id and estimated wait time.
    """
    return json.dumps(
        {
            "ticket_id": f"ESC-{hash(reason) % 10000:04d}",
            "priority": priority,
            "reason": reason,
            "estimated_wait": "5 minutes" if priority == "urgent" else "2 hours",
            "status": "created",
        }
    )


# ---------------------------------------------------------------------------
# Mock public docs tools (replace real MCP when USE_MOCK_TOOLS=1)
# ---------------------------------------------------------------------------


@lc_tool
def knowledge_base_retrieve(query: str, top_k: int = 3) -> str:
    """Mock: Search the Foundry IQ knowledge base (reads local external docs).

    Args:
        query: Natural language search query.
        top_k: Maximum results to return.

    Returns:
        JSON list of matching document summaries.
    """
    results = []
    for doc_file in sorted(EXTERNAL_DOCS_DIR.glob("*.md")):
        content = doc_file.read_text()
        if any(word.lower() in content.lower() for word in query.split()):
            results.append(
                {
                    "doc_id": doc_file.stem,
                    "title": content.split("\n")[0].lstrip("# "),
                    "snippet": content[:300],
                    "source": f"https://learn.microsoft.com/azure/ai-studio/{doc_file.stem}",
                    "authority": "official",
                }
            )
    return json.dumps(results[:top_k])


@lc_tool
def microsoft_docs_search(query: str, top_k: int = 5) -> str:
    """Mock: Search Microsoft Learn documentation (reads local external docs).

    Args:
        query: Natural language search query.
        top_k: Maximum results to return.

    Returns:
        JSON list of matching document summaries.
    """
    results = []
    for doc_file in sorted(EXTERNAL_DOCS_DIR.glob("*.md")):
        content = doc_file.read_text()
        if any(word.lower() in content.lower() for word in query.split()):
            url = f"https://learn.microsoft.com/azure/ai-studio/{doc_file.stem}"
            results.append(
                {
                    "title": content.split("\n")[0].lstrip("# "),
                    "url": url,
                    "snippet": content[:300],
                }
            )
    return json.dumps(results[:top_k])


@lc_tool
def microsoft_docs_fetch(url: str) -> str:
    """Mock: Fetch full content of a Microsoft Learn documentation page.

    Args:
        url: The documentation page URL (matched by stem to local files).

    Returns:
        Full page content in markdown format.
    """
    # Extract the doc stem from the URL to match local files
    stem = url.rstrip("/").split("/")[-1]
    doc_path = EXTERNAL_DOCS_DIR / f"{stem}.md"
    if not doc_path.exists():
        # Try fuzzy match
        for candidate in EXTERNAL_DOCS_DIR.glob("*.md"):
            if stem.replace("-", "") in candidate.stem.replace("-", ""):
                doc_path = candidate
                break
    if not doc_path.exists():
        return json.dumps({"error": f"Page not found: {url}"})
    return doc_path.read_text()


# ---------------------------------------------------------------------------
# Identity verification tool (simulates auth / session token check)
# ---------------------------------------------------------------------------


@lc_tool
def verify_caller_identity(user_claim: str) -> str:
    """Verify the caller's identity and return their clearance level.

    In production this validates the session token against the identity
    provider.  Call this before sharing any internal documentation.

    Args:
        user_claim: How the caller identified themselves (name, team, role,
            or any context about who they are).

    Returns:
        JSON with identity status and content-access levels.
    """
    internal_indicators = [
        "engineer",
        "team",
        "internal",
        "platform",
        "on-call",
        "sre",
        "sde",
        "developer",
        "architect",
        "ops",
        "infra",
        "devops",
        "@microsoft",
        "employee",
        "colleague",
        "our team",
        "my team",
        "sprint",
        "standup",
        "incident",
    ]
    claim_lower = user_claim.lower()
    is_internal = any(ind in claim_lower for ind in internal_indicators)

    if is_internal:
        return json.dumps(
            {
                "identity": "verified_internal",
                "clearance": "internal",
                "can_access": ["public", "internal"],
                "restricted": ["confidential"],
            }
        )
    return json.dumps(
        {
            "identity": "external_user",
            "clearance": "external",
            "can_access": ["public"],
            "restricted": ["internal", "confidential"],
        }
    )
