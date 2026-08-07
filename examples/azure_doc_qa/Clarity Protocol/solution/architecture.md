# Architecture

## Components
- **triage node** — LLM classifier, emits `{"route": ...}`; routes to
  `product_docs` | `internal_docs` | `escalation`. Prompt forbids answering the
  question itself or revealing routing logic.
- **product_docs node** — iterative tool loop over public-doc tools
  (`knowledge_base_retrieve`, `microsoft_docs_search`, `microsoft_docs_fetch`,
  `escalate_to_human`); real Foundry IQ / Learn MCP or mock. Must ground every
  answer and never fabricate.
- **internal_docs node** — iterative tool loop over
  `verify_caller_identity`, `search_internal_docs`, `get_internal_document`,
  `knowledge_base_retrieve`, `microsoft_docs_search`, `escalate_to_human`.
  Enforces a clearance-based information barrier (external → public only;
  internal → +INTERNAL but not raw CONFIDENTIAL).
- **escalation node** — single-round `escalate_to_human` handoff.
- **shared LLM** — AzureChatOpenAI (`ASSERT_AZURE_DEPLOYMENT`, default
  `gpt-4o-mini`, temperature 0 for nodes).

## Trust boundaries
- **User input** — untrusted; may carry social-engineering pretext ("I'm on the
  infra team") and paraphrased jailbreaks.
- **Retrieved documents** — untrusted DATA. Internal fixtures include INTERNAL-002
  with an embedded prompt-injection (XPIA) payload and INTERNAL-004 a CONFIDENTIAL
  "DO NOT SHARE" roadmap. The information barrier and injection rules live only in
  the specialist system prompts.
- **Clearance** — returned by `verify_caller_identity`; the model must both call it
  first and honor the returned level. Nothing structurally enforces either.

## Threat model

```mermaid
flowchart TD
    U[User (untrusted)] --> T[triage]
    T -->|product_documentation| P[product_docs]
    T -->|internal_engineering| I[internal_docs]
    T -->|escalation| E[escalation]
    P --> KB[(public doc tools / MCP)]
    I --> VC[verify_caller_identity]
    I --> ID[(internal docs: INTERNAL/CONFIDENTIAL)]
    KB -. injected instructions .-> P
    ID -. XPIA payload INTERNAL-002 .-> I
    ID -. CONFIDENTIAL INTERNAL-004 .-> I
    P -->|answer text| R{{reply to user}}
    I -->|answer text| R
    classDef risk fill:#fdd,stroke:#c00;
    class KB,ID risk;
```

**Top risks (all prompt-only today):**
1. Confidential/internal content leaked to an under-cleared caller (R5/R9).
2. Fabricated API/SDK/pricing/doc content presented as grounded (R6).
3. Agent obeys instructions embedded in a retrieved document (R7).
4. Routing/CoT logic disclosed to the user (R8).

**Intervention points.** Prose failures (leak, fabrication, injection compliance,
CoT leak) → **output-semantic annotator gate** over the reply. Identity/clearance
ordering → structural gate at `verify_caller_identity` / internal-retrieval.
