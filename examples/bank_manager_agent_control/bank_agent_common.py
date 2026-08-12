"""Bank support agent demo — shared agent plumbing.

This module holds the pieces that are *not* part of any behavior's claim: how to
build the target LLM, how to open the two MCP servers (a realistic multi-domain
bank + a policy knowledge base), how to pull the final assistant text out of a
LangGraph state, and the ACS-manifest path workaround.

The behaviors themselves live next door and own their own prompts, arms, and
control surfaces:

  - ``eval_tier_authorization*.yaml`` + ``agent_tier_authz.py``
    — sensitivity-tier authorization (deterministic; ACS Rego).
  - ``eval_coercion_*.yaml`` + ``coercion_agent.py``
    — coercion via unverified authority (non-deterministic; ACS classifier
      annotator).

Nothing here declares a system prompt or an ASSERT callable, on purpose: a
shared module that also shipped a baseline agent is how the previous version of
this demo ended up with a baseline nobody would defend in review.

See https://github.com/responsibleai/AgentControlSpecification for the ACS spec
and SDKs.
"""
from __future__ import annotations

# Auto-trace LangChain / OpenAI / MCP via OpenInference — lazy. Installs the
# OpenInference instrumentors locally; only imports phoenix.otel / exports when a
# Phoenix collector is reachable, so imports stay fast. Run `phoenix serve`
# first, or set PHOENIX_COLLECTOR_ENDPOINT, to also export the spans.
from assert_ai import auto_trace; auto_trace.enable()

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_mcp_adapters.tools import load_mcp_tools
from langchain_openai import AzureChatOpenAI
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

# ── Paths ─────────────────────────────────────────────────────────────────
EXAMPLE_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = EXAMPLE_DIR / "runtime"
if str(RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, str(RUNTIME_DIR))

# Realistic multi-domain bank + knowledge-base servers. Both behaviors' agents
# connect to this same pair, so the tool surface is a constant across behaviors.
MCP_SERVER_BANK = RUNTIME_DIR / "realistic_bank_mcp_server.py"
MCP_SERVER_KB = RUNTIME_DIR / "kb_mcp_server.py"


def _build_llm() -> BaseChatModel:
    """Build the target agent's LLM, routing by AGENT_MODEL name.

    Reads AZURE_API_KEY / AZURE_API_BASE from the environment (.env loaded
    above). Override the model via the AGENT_MODEL env var.

    - GPT-family deployments (model name starts with ``gpt``) are served via
      the Azure OpenAI gateway (``/openai/deployments/{name}``) and use
      ``AzureChatOpenAI``.
    - Everything else (DeepSeek, Mistral, Llama, Phi, Cohere, …) is served
      via Azure AI Inference (``/models/chat/completions``) and uses
      ``AzureAIChatCompletionsModel`` from ``langchain-azure-ai``. These
      deployments typically run behind SGLang/vLLM, which require the
      ``model`` body field to be populated — something Azure OpenAI's path
      style does not do.
    """
    deployment = os.environ.get("AGENT_MODEL", "gpt-4o-mini")

    base = os.environ["AZURE_API_BASE"]
    key = os.environ.get("AZURE_API_KEY") or ""

    # Entra/AAD path: when ASSERT_AZURE_USE_AAD=1 (a key-auth-disabled resource),
    # authenticate the SUT with an az-login bearer-token
    # provider instead of a static key. The provider auto-refreshes, so long runs
    # do not hit token expiry. aad_auth lives next to this module.
    try:
        import aad_auth
        _use_aad = aad_auth.use_aad()
    except Exception:
        _use_aad = False

    if deployment.lower().startswith("gpt"):
        # gpt-5* deployments reject temperature != 1 (only default supported).
        # Older gpt-4* / gpt-3.5 deployments accept temperature=0 for
        # deterministic eval runs. Branch here so the same callable works
        # for both. max_tokens via max_completion_tokens for newer models.
        kwargs: dict = dict(
            azure_deployment=deployment,
            azure_endpoint=base,
            api_version=os.environ.get("AZURE_API_VERSION", "2024-12-01-preview"),
            max_tokens=4000,
        )
        if _use_aad:
            kwargs["azure_ad_token_provider"] = aad_auth.get_provider()
        else:
            kwargs["api_key"] = key
        if not deployment.lower().startswith("gpt-5"):
            kwargs["temperature"] = 0.0
        return AzureChatOpenAI(**kwargs)

    # Azure AI Inference route — endpoint is ``{base}/models``, deployment
    # passed as ``model`` (populates the request body field SGLang requires).
    from langchain_azure_ai.chat_models import AzureAIChatCompletionsModel

    inference_endpoint = base.rstrip("/") + "/models"
    return AzureAIChatCompletionsModel(
        endpoint=inference_endpoint,
        credential=key,
        model=deployment,
        temperature=0.0,
        max_tokens=4000,
    )


# ── Text extraction ────────────────────────────────────────────────────────

def _extract_text(result: object) -> str:
    """Extract the last assistant text from a LangGraph state dict or string."""
    if isinstance(result, str):
        return result
    if isinstance(result, dict) and "messages" in result:
        for msg in reversed(result["messages"]):
            if isinstance(msg, AIMessage) and msg.content:
                return str(msg.content)
        msgs = result["messages"]
        if msgs:
            last = msgs[-1]
            return str(getattr(last, "content", last))
    return str(result)


ACS_POLICY_DIR = EXAMPLE_DIR / "acs" / "policy"


def _acs_manifest_with_absolute_bundle(manifest: Path) -> Path:
    """Return a manifest path whose ``bundle:`` is an absolute filesystem path.

    Workaround for an ACS 0.1.0 bug on Windows: when the manifest declares
    ``bundle: ./policy`` the bundled OPA dispatcher fails silently with
    ``runtime_error:policy_invocation_failed``. Using an absolute path
    works reliably. We rewrite the manifest into a per-session temp file
    on import so the on-disk source manifest stays portable.
    """
    import re
    import tempfile

    source = manifest.read_text(encoding="utf-8")
    abs_bundle = (manifest.parent / "policy").resolve().as_posix()
    rewritten = re.sub(
        r"^(\s*bundle:\s*)\.?/?policy\s*$",
        lambda m: f"{m.group(1)}{abs_bundle}",
        source,
        count=1,
        flags=re.MULTILINE,
    )
    tmp_dir = Path(tempfile.gettempdir()) / "acs_bank_manager"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    rewritten_path = tmp_dir / manifest.name
    rewritten_path.write_text(rewritten, encoding="utf-8")
    return rewritten_path


# ── MCP servers (bank + knowledge base) ────────────────────────────────────

async def _open_two_servers(stack):
    """Open the bank + KB MCP stdio sessions on an AsyncExitStack and return the
    concatenated tool list (bank tools first, then the knowledge_base tool)."""
    bank_params = StdioServerParameters(command=sys.executable, args=[str(MCP_SERVER_BANK)])
    kb_params = StdioServerParameters(command=sys.executable, args=[str(MCP_SERVER_KB)])

    br, bw = await stack.enter_async_context(stdio_client(bank_params))
    bank_session = await stack.enter_async_context(ClientSession(br, bw))
    await bank_session.initialize()

    kr, kw = await stack.enter_async_context(stdio_client(kb_params))
    kb_session = await stack.enter_async_context(ClientSession(kr, kw))
    await kb_session.initialize()

    bank_tools = await load_mcp_tools(bank_session)
    kb_tools = await load_mcp_tools(kb_session)
    return bank_tools + kb_tools
