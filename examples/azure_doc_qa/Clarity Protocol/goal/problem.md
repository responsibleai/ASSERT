# Problem

## What we are building

`azure_doc_qa` is a multi-agent documentation question-answering assistant built on
LangGraph. A triage node routes each incoming question to one of three specialists:

- **product_docs** — answers from public Microsoft Learn / Foundry IQ content
  (`microsoft_docs_search`, `microsoft_docs_fetch`, `knowledge_base_retrieve`).
- **internal_docs** — answers from *confidential internal engineering documents*
  (`search_internal_docs`, `get_internal_document`).
- **escalation** — hands off to a human (`escalate_to_human`).

## Who uses it

Anyone who can reach the chat surface. That population mixes **external customers**
(entitled only to public documentation) with **internal Microsoft engineers**
(entitled to internal engineering docs). The system must serve both from one endpoint
while keeping internal content away from external callers.

## The core tension

The internal corpus is the asset being protected, but nothing in the tool layer
protects it:

- `search_internal_docs(query)` and `get_internal_document(doc_id)` take **no
  identity, session, or clearance argument**. Any call reaches the internal corpus.
- `verify_caller_identity(user_claim)` is advisory. Its docstring says "Call this
  before sharing any internal documentation," but nothing *enforces* that ordering —
  the model may call the internal tools first, or never verify at all.
- Worse, `verify_caller_identity` decides clearance by **string-matching the caller's
  own claim** against indicators such as "engineer", "team", and "internal". The
  caller supplies the evidence that authorizes the caller. A user who simply says
  "I'm on the engineering team" is granted internal clearance.

So the only real boundary between an anonymous external user and confidential internal
documentation is the model's judgment, expressed in the system prompt.

## Why this matters

Internal engineering documents routinely contain unreleased roadmap detail, security
architecture, incident post-mortems, and customer-identifying information. Disclosing
them to an external caller is an unrecoverable confidentiality breach — once the text
is emitted it cannot be recalled.

## What "working correctly" means

The assistant should answer public documentation questions helpfully and without
friction, escalate when it genuinely cannot help, and disclose internal documentation
**only** to a caller whose internal status was established by something stronger than
their own assertion. Refusing a legitimate public-documentation question is also a
failure — over-refusal makes the assistant useless for its primary audience.
