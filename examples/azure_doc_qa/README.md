# Azure Doc QA — Multi-Agent RAG Evaluation

This example demonstrates evaluating a **multi-agent RAG system** that answers
questions about Azure AI Foundry documentation. It showcases:

- **Multi-agent orchestration**: Triage → ProductDocs / InternalDocs / Escalation
- **Real MCP tool integration**: Foundry IQ + Microsoft Learn MCP servers
- **Information barrier enforcement**: Public vs. confidential internal docs
- **Adversarial resilience**: Prompt injection in retrieved docs, CoT leakage
- **Identity verification**: Clearance-based access control for internal docs
- **Iterative tool-call loop**: Multi-round tool execution within each agent node
- **Eval-driven development**: the agent was hardened over several rounds of
  eval-and-fix, documented in [IMPROVEMENT_JOURNEY.md](IMPROVEMENT_JOURNEY.md)

## What's in this directory

| Path | What it is |
|---|---|
| `agent.py` | The multi-agent system itself. Exposes `chat`, the callable ASSERT evaluates. |
| `mock_tools.py` | Offline retrieval tools over `docs/`, used when `USE_MOCK_TOOLS=1`. |
| `mcp_tools.py` | Real MCP client wiring for Foundry IQ and Microsoft Learn. |
| `docs/` | The fictional public + internal document corpus the agent retrieves from. |
| `evals/<atomic_behavior>.yaml` | One ASSERT eval suite per behavior — behavior taxonomy, test-set generation, target, and judge. |
| `IMPROVEMENT_JOURNEY.md` | The eval-driven-development log — what each round of failures changed in the agent. |
| `auto_trace.py` | Legacy tracing shim. Not used by the current configs: ASSERT installs the instrumentors itself when `target.trace` is set. |
| `README.md` | This file. |

## Architecture

```
User Question
    │
    ▼
┌──────────┐     ┌─────────────────┐
│  Triage  │────►│ ProductDocsAgent │ ← Foundry IQ MCP + Learn MCP
│  Agent   │     └─────────────────┘
│          │     ┌─────────────────┐
│          │────►│ InternalDocsAgent│ ← Identity verification + internal docs
│          │     └─────────────────┘
│          │     ┌─────────────────┐
│          │────►│   Escalation    │ ← Human handoff
└──────────┘     └─────────────────┘
```

Each specialist node runs an **iterative tool-call loop** (up to 3 rounds),
allowing multi-step workflows like: verify identity → search docs → retrieve
full text → synthesize answer.

## The two measured risks

| Risk | Failure mode |
|---|---|
| `confidential_internal_leakage.yaml` | Discloses internal-only content to a user without the clearance to see it |
| `fabricated_ungrounded_answer.yaml` | Answers with detail the retrieved documents do not support, or attributes it to a source that does not say it |

Each risk gets its own suite under `evals/`, so the two are measured independently.

## Quick Start (Mock Mode — No Auth Required)

```bash
# From the repo root
python -m pip install -e ".[otel,langgraph]"
cp .env.example .env   # set AZURE_API_BASE, AZURE_API_KEY, ASSERT_AZURE_DEPLOYMENT

# Run eval with mock tools (offline, no MCP servers needed)
USE_MOCK_TOOLS=1 assert-ai run --config examples/azure_doc_qa/evals/confidential_internal_leakage.yaml
USE_MOCK_TOOLS=1 assert-ai run --config examples/azure_doc_qa/evals/fabricated_ungrounded_answer.yaml
```

## Real MCP Mode

Requires Foundry IQ and Microsoft Learn MCP server access:

```bash
# Additional env vars for real MCP mode
export FOUNDRY_IQ_MCP_ENDPOINT="https://your-foundry-iq-endpoint/sse"
export FOUNDRY_IQ_TOKEN="your-bearer-token"
# Node.js required for Learn MCP (npx -y @microsoftdocs/mcp)

# Run without USE_MOCK_TOOLS (real MCP tools used)
assert-ai run --config examples/azure_doc_qa/evals/confidential_internal_leakage.yaml
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `AZURE_API_BASE` | Yes | Azure OpenAI endpoint URL |
| `AZURE_API_KEY` | Yes | Azure OpenAI API key |
| `ASSERT_AZURE_DEPLOYMENT` | No | Model deployment name (default: `gpt-4o-mini`) |
| `USE_MOCK_TOOLS` | No | Set to `1` for offline mock mode |
| `FOUNDRY_IQ_MCP_ENDPOINT` | Real mode | Foundry IQ SSE endpoint |
| `FOUNDRY_IQ_TOKEN` | Real mode | Foundry IQ bearer token |

## Internal Docs (Fake — For Evaluation Only)

The `docs/internal/` directory contains **entirely fictional** engineering
documents designed to test information barrier enforcement and adversarial
resilience. They are not real Microsoft internal documents.

| Doc ID | Challenge |
|--------|-----------|
| INTERNAL-001 | Confidential architecture — must not leak to external users |
| INTERNAL-002 | Contains embedded prompt injection (XPIA) payload |
| INTERNAL-003 | Internal-only incident response procedures |
| INTERNAL-004 | Confidential roadmap — "DO NOT SHARE" |
| INTERNAL-005 | Version conflict with public docs (v2.3 vs v2.4) |

## Judge Dimensions

Each response is scored on two dimensions:

| Dimension | What it asks |
|---|---|
| Impermissible Behavior violated | Did the agent violate a behavior the eval spec does **not** permit? This is the harm number. |
| Permissible Behavior violated | Did the agent violate a behavior the eval spec **does** permit? This is the trade-off number, read next to harm rather than after it. |

Both are built in — ASSERT adds them to every run. The behaviour taxonomy in
each suite is what makes them specific: violations in the leakage suite are
judged against leakage behaviours, and in the fabrication suite against
grounding behaviours. Every flagged violation is classified as permissible or
non-permissible, and that split is what produces the two headline metrics above
— so the harm number reads as harm rather than as raw rule-breaking.

## Expected Output

Each suite writes to `artifacts/results/<suite>/` —
`azure-doc-qa-confidential-leakage` and `azure-doc-qa-fabricated-answer`:

| File | What it holds |
|---|---|
| `taxonomy.json` | Auto-generated behavior categories |
| `test_set.jsonl` | 50 stratified test cases (25 prompt + 25 scenario) |
| `suite.json`, `stratification.json`, `systematization.json` | How the suite was built |
| `baseline/inference_set.jsonl` | Agent responses, with the tool trace per case |
| `baseline/scores.jsonl` | Per-test-case judge verdicts and justifications |
| `baseline/metrics.json` | Aggregate Impermissible Behavior violated and Permissible Behavior violated rates |
| `baseline/config.yaml`, `baseline/manifest.json` | Exactly what was run |

`artifacts/` is gitignored, so runs stay local and are never committed.
