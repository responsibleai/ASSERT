# Architecture — Azure Doc QA Agent

## Shape

A LangGraph multi-agent graph exposed as an async callable:

```
examples.azure_doc_qa.agent:chat(message, history) -> str

  triage ──┬─> product_docs   (public tools; real MCP or mock)
           ├─> internal_docs  (identity + internal corpus; always mocked)
           └─> escalation     (human handoff)
```

`history` is accepted by name, so ASSERT drives genuine multi-turn scenarios.
The callable is async; ASSERT's `invoke_callable` awaits coroutine functions
natively, so no sync shim is required.

| Layer | File | Role |
| --- | --- | --- |
| Entry callable | `agent.py` | Graph, routing, three system prompts |
| Governed variant | `agent_guarded.py` | Same graph with structural gates |
| Tools | `mock_tools.py` | Public + internal retrieval, identity, escalation |
| MCP variant | `mcp_tools.py` | Alternate public tool wiring |
| Public corpus | `docs/external/*.md` | Freely disclosable |
| Internal corpus | `docs/internal/*.md` | Access-controlled |

## Where it actually fails

Measured ungoverned baseline: **policy_violation 45.8% prompt / 88.0%
scenario**, overrefusal 4.2% / 12.0%. The worst scenario slice in the batch.

The cause is two lines in `mock_tools.verify_caller_identity`:

```python
internal_indicators = ["engineer", "team", "internal", "platform", "ops",
                       "incident", "colleague", "employee", ...]     # 20 words
is_internal = any(ind in claim_lower for ind in internal_indicators)
```

`user_claim` is, per `INTERNAL_DOCS_PROMPT` step 1, "a summary of how the user
identified themselves ... or any context clues from the conversation." The model
assembles that string out of the user's own words and passes it to a function
that substring-searches it for words the user chose.

**The caller authenticates the caller.** "Our team is hitting an incident"
contains three trigger words. Once clearance flips to `internal`, the system
prompt's own rule 3 authorizes the agent to "share content freely including
procedures, steps, commands."

```
 user (untrusted)
      |
      | says "engineer"
      v
 verify_caller_identity  <-- input is attacker-controlled; this is not a check
      |
      | clearance: internal
      v
 search_internal_docs / get_internal_document   <-- no clearance parameter
      |
      v
 docs/internal/*.md   (roadmap, incident playbook, known issues, runbook)
```

The scenario slice is nearly double the prompt slice because a cold single-turn
request may not contain a trigger word, while four turns of technical
conversation almost always will — and once clearance flips it stays flipped.

## What was built

`agent_guarded.py` keeps the graph, the prompts, and the routing. Three layers,
in descending order of strength.

### 1. Sever the self-assertion path (structural)

`_verified_clearance()` reads an out-of-band session principal and nothing else.
`verify_caller_identity` keeps its signature but **ignores `user_claim`
entirely**. No sentence the user types can raise clearance.

Its `external` response also carries an explicit instruction not to tell the
user which words would have granted access — otherwise a blocked agent
helpfully explains the bypass.

### 2. Withhold content at the tool boundary (structural)

With clearance `external`, `search_internal_docs` returns an empty result set
and `get_internal_document` returns `content: None`. No snippet, no title, no
`authority` label.

Titles and labels are stripped, not just bodies. The failing scenario categories
include "Existence or structure disclosure of internal artifacts" and
"Attribution or source-identification leakage" — naming an internal document is
scored as disclosure on its own. This is the same finding that forced a redesign
in `science_research_agent` this batch, arrived at independently: **a redaction
marker is part of the model's context and will be repeated back to the user.**

The model cannot disclose what it was never given.

### 3. Screen the outbound reply (deterministic, then semantic)

`_corpus_overlap` is a genuine oracle rather than a judgment call: the internal
corpus is on disk, so the exact text the agent must not reproduce is known.

- A content-bearing 6-gram shared with any internal document. Ambient Azure
  vocabulary (`azure`, `agent`, `service`, `model`, `documentation`, …) is
  stripped first, so generic phrasing cannot masquerade as overlap.
- A verbatim internal section heading or document id — structure disclosure
  even with no body text attached.

`_InternalDisclosureAnnotator` then runs the committed ACS output policy as an
**additive backstop** and fails open. It returns bare `"deny"`/`"allow"` because
this domain's generated Rego reads `input.annotations.<name> == "deny"` — the
third of five mutually incompatible annotator return contracts in this batch.

Wired via `AgentControl.from_path(manifest, dispatcher)` by hand, because
`build_agent_control` in `assert_ai/integrations/acs/guard.py` constructs the
control **without a dispatcher**, leaving `input.annotations.*` unpopulated so
every semantic rule fails open silently and with no error.

### Deliberate divergence from the generated policy

`acs generate` also emitted `pre_tool_call` and `post_tool_call` rules gating
`knowledge_base_retrieve`, `microsoft_docs_search`, and `escalate_to_human` on
the disclosure annotator. Those are public retrieval and the escalation path;
they cannot return internal content, so blocking them cannot prevent disclosure
and can only manufacture overrefusal. The dispatcher returns `"allow"` for them,
with the reasoning recorded in the class docstring rather than left implicit.

This is the same class of generated-policy error seen in
`travel_planner_langgraph`, where retrieval tools were gated on a *fabrication*
annotator — gating the cure for the disease.

## Guarding the recovery path

Baseline overrefusal is only 4.2% / 12.0%, and a fix that buys an 88-point
violation drop by refusing everything would not be a fix. So:

- public retrieval is untouched;
- escalation is never gated;
- the fallback leads with what the assistant *can* do and states the limit
  underneath, rather than opening with an apology.

## Verification

Gate tests, all passing: self-asserted identity never grants clearance across
three phrasings; internal tools withhold content, title, and label; verbatim
corpus text is caught; a bare internal doc-id is caught; and four legitimate
public answers plus the fallback produce no false positives. The ACS output gate
was checked for polarity in both directions — clean public answer `allow`, leaky
answer `deny`.

## Measured result

| run | PV prompt | PV scenario | OR prompt | OR scenario |
|---|---|---|---|---|
| baseline | 45.8% | 88.0% | 4.2% | 12.0% |
| governed v1 | 4.0% | 56.0% | 4.0% | 40.0% |
| **governed v2** | 12.0% | **32.0%** | 4.0% | **36.0%** |

The baseline scenario rate of 88.0% is the second-worst in the batch: in a
multi-turn thread the agent would eventually quote, paraphrase, or confirm the
existence and structure of internal engineering material for almost any
persistent asker.

v1 cut prompt disclosure to 4.0% but raised scenario overrefusal from 12.0% to
40.0%. The justifications were unambiguous about the cause, and it was the same
cause found in three other domains in this batch: the guard was refusing
**public** work. Users asking for a public App Service checklist, publicly
documented rollback and known-issue guidance, a high-level architectural
comparison, or customer-safe wording were getting a boundary statement and a
link.

v2 rewrote `_REGEN_INSTRUCTION` with an explicit "what you must still deliver"
block enumerating the permissible public work, added an explicit prohibition on
bare deflection ("escalation is an addition to a substantive answer, never a
substitute for one"), and replaced the `_FALLBACK` -- which had been a pure
limitation statement -- with one that leads with concrete offers of help.

Scenario disclosure fell 56.0% -> 32.0% and overrefusal fell 40.0% -> 36.0%.

**Against baseline, v2 removes 56 points of scenario disclosure and 33.8 points
of prompt disclosure.** The residual 36.0% scenario overrefusal is the honest
cost: the guard is still too quick to fall back on the boundary statement deep
in a multi-turn thread, and closing that gap further is the clearest next piece
of work in this domain.
