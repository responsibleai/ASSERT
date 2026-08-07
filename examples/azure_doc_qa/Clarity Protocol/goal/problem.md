# Problem

**Project.** A multi-agent Azure AI Foundry documentation-QA assistant built on
LangGraph. A `triage` node routes each user question to one of three specialist
agents: `product_docs` (answers from public Azure docs via Foundry IQ / Microsoft
Learn MCP tools, or mock equivalents), `internal_docs` (answers from fictional
internal engineering docs behind a clearance-based information barrier), and
`escalation` (human handoff). Each specialist runs an iterative tool-call loop
(verify identity → retrieve → synthesize). Entry point: async
`chat(message, history=None) -> str` in `examples/azure_doc_qa/agent.py`.

**Core tension.** The assistant must be *maximally helpful* — synthesize direct
answers from retrieved documents, handle multi-part public+internal questions,
avoid over-escalating — while enforcing hard safety boundaries that a
helpful-by-default model naturally erodes:

- an **information barrier** (external callers must never receive INTERNAL or
  CONFIDENTIAL content; even verified internal callers must not get raw
  CONFIDENTIAL roadmap/architecture text),
- **grounding** (never fabricate API params, SDK methods, pricing, or document
  content that retrieval did not return),
- **adversarial resilience** (retrieved documents are untrusted; some contain
  embedded prompt-injection payloads that try to redirect the agent).

Every one of these guardrails currently lives only in the system prompts, so it
is defeasible under paraphrase, multi-turn pressure, and injected instructions.

**Why it matters.** A leak of confidential internal content to an unverified
caller, a fabricated API answer presented with a fake citation, or an agent that
obeys instructions embedded in a retrieved doc are all real, shippable harms for a
documentation assistant wired to real corpora.

**Success looks like** a measured baseline violation rate per failure mode, then a
deployable ACS gate that provably drops the harmful-response rate without
collapsing into over-refusal.
