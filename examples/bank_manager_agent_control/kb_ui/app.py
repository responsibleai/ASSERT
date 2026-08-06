from __future__ import annotations

import asyncio
import inspect
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

BASE_DIR = Path(__file__).resolve().parent
EXAMPLE_DIR = BASE_DIR.parent
REPO_ROOT = EXAMPLE_DIR.parent.parent
POLICY_PATH = EXAMPLE_DIR / "acs" / "policy" / "bank_manager_feature.rego"
GROUNDING_GATE_QUERY = "data.agent_control_specification.bank_manager_feature.verdict"

# Load repo-root .env so the launch one-liner is self-sufficient (SEARCH_*, KB_BACKEND).
try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass

RUNTIME_DIR = EXAMPLE_DIR / "runtime"
if str(RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, str(RUNTIME_DIR))

from kb_backend import get_backend  # noqa: E402

backend = get_backend()


async def index(_: Request) -> FileResponse:
    return FileResponse(BASE_DIR / "index.html")


async def health(_: Request) -> JSONResponse:
    """Report which KB backend is actually live, so the UI can label itself
    truthfully (Foundry IQ vs the offline mock) instead of a hardcoded pill."""
    name = type(backend).__name__
    label = "Foundry IQ — live" if name == "FoundryIQBackend" else "offline mock backend"
    return JSONResponse({"backend": name, "label": label})


async def _retrieve(question: str) -> dict[str, Any]:
    retrieve = backend.retrieve
    if inspect.iscoroutinefunction(retrieve):
        return await retrieve(question)
    return await asyncio.to_thread(retrieve, question)


async def _run_grounding_gate(kb_result: dict[str, Any]) -> dict[str, str]:
    gate_input = {
        "intervention_point": "post_tool_call",
        "tool": {"name": "knowledge_base_retrieve"},
        "policy_target": {"value": kb_result},
    }

    proc = await asyncio.to_thread(
        subprocess.run,
        [
            "opa",
            "eval",
            "-f",
            "raw",
            "-I",
            "-d",
            str(POLICY_PATH),
            GROUNDING_GATE_QUERY,
        ],
        input=json.dumps(gate_input),
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return {
            "decision": "error",
            "reason": "opa_eval_failed",
            "message": proc.stderr.strip() or "OPA grounding gate failed.",
        }

    verdict = json.loads(proc.stdout or '{"decision":"allow"}')
    decision = str(verdict.get("decision", "allow"))
    return {
        "decision": decision,
        "reason": str(verdict.get("reason") or ("grounded_policy_claim" if decision == "allow" else "")),
        "message": str(
            verdict.get("message")
            or ("Grounded answer may be shown with citations." if decision == "allow" else "")
        ),
    }


def _normalize_citations(kb_result: dict[str, Any]) -> list[dict[str, Any]]:
    citations = []
    for item in kb_result.get("citations", []) or []:
        citation: dict[str, Any] = {
            "source": item.get("source") or item.get("ref_id") or "unknown",
        }
        if item.get("ref_id"):
            citation["ref_id"] = item["ref_id"]
        if item.get("score") is not None:
            citation["score"] = item["score"]
        if item.get("snippet"):
            citation["snippet"] = item["snippet"]
        citations.append(citation)
    return citations


async def ask(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
        question = str(payload.get("question", "")).strip()
        if not question:
            return JSONResponse({"error": "question is required"}, status_code=400)

        kb_result = await _retrieve(question)
        gate = await _run_grounding_gate(kb_result)
        return JSONResponse(
            {
                "question": question,
                "answer": kb_result.get("answer", ""),
                "citations": _normalize_citations(kb_result),
                "grounded": bool(kb_result.get("grounded", False)),
                "gate": gate,
            }
        )
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            {
                "error": f"{type(exc).__name__}: {exc}",
                "gate": {
                    "decision": "error",
                    "reason": "request_failed",
                    "message": "The local KB demo request failed.",
                },
            },
            status_code=500,
        )


app = Starlette(
    debug=False,
    routes=[
        Route("/", index, methods=["GET"]),
        Route("/health", health, methods=["GET"]),
        Route("/ask", ask, methods=["POST"]),
    ],
)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8800, log_level="info")
