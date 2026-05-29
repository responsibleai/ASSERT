# Improving an Agent with Eval-Driven Development

This document walks through how we used **ASSERT** (Adaptive Eval) to iteratively
improve a multi-agent RAG system from a **~20% pass rate to 82%** over 7 rounds
of targeted fixes. It demonstrates the **eval → diagnose → fix → re-eval** loop
that makes agent development systematic rather than guesswork.

## The Agent Under Test

The `azure_doc_qa` agent is a LangGraph multi-agent system with three specialist
nodes:

```
User Question → Triage → ProductDocs / InternalDocs / Escalation → Response
```

- **Triage** classifies questions and routes to the right specialist
- **ProductDocs** retrieves and synthesizes public Azure AI Foundry documentation
- **InternalDocs** handles internal engineering questions with access controls
- **Escalation** creates human support tickets for complaints and bug reports

The eval config defines 9 judge dimensions (hallucination, attribution error,
boundary violation, prompt injection, workflow violation, escalation judgment,
wrong tool selection, policy violation, and overrefusal) and generates 56 test
cases across different question types and adversarial pressures.

---

## Round 1: From ~20% to 61%

### Step 1 — Run the baseline eval

```bash
USE_MOCK_TOOLS=1 assert-eval run --config examples/azure_doc_qa/eval_config.yaml
```

The initial run showed a **~80% policy_violation rate** — nearly every test case
was failing. The `metrics.json` breakdown revealed that `policy_violation` was
the dominant failure dimension, appearing in almost all failed cases.

### Step 2 — Diagnose with artifacts

We examined the artifacts in `artifacts/results/azure-doc-qa-v1/demo-1/`:

1. **`scores.jsonl`** — Judge verdicts per test case per dimension, with
   justification text explaining each failure
2. **`inference_set.jsonl`** — Full agent conversation transcripts showing
   exactly what the agent said

Reading the judge justifications surfaced a clear pattern: the agent was
outputting raw JSON routing objects like `{"route": "product_documentation",
"reason": "..."}` as part of its responses. The judge correctly flagged this
as a policy violation — internal routing metadata should never be visible to
the user.

### Step 3 — Root cause analysis

Tracing the issue through the code revealed **three related bugs**:

**Bug 1 — Triage node poisoned the message history.** The `triage()` function
added its routing JSON as an `AIMessage` to the graph state. Every downstream
specialist saw this routing artifact in the conversation and sometimes echoed it.

**Bug 2 — Specialist nodes used tool-bound LLM for synthesis.** After retrieving
documents, the specialist called the same tool-bound LLM instance for the
synthesis response. The LLM sometimes attempted tool calls instead of generating
a natural language answer.

**Bug 3 — Response extraction was fragile.** The `chat()` function used reverse
scanning to find the last AI message, but had no filter for routing JSON
artifacts. Even when bugs 1 and 2 were fixed, edge cases could still surface.

### Step 4 — Apply targeted fixes

Each fix was a small, focused commit:

| Commit | Fix | What changed |
|--------|-----|-------------|
| `b8dc13d` | Stop leaking routing JSON | `triage()` returns only `{"route": route}` — no `AIMessage` |
| `6c32118` | Use un-bound LLM for synthesis | Specialist followup calls use `_get_llm()` instead of tool-bound instance |
| `49f40e2` | Filter routing artifacts | `chat()` skips messages whose content parses as `{"route": ...}` |

### Step 5 — Re-evaluate

```bash
USE_MOCK_TOOLS=1 assert-eval run --config examples/azure_doc_qa/eval_config.yaml
```

Result: **34/56 passing (61%)**, up from ~20%. The routing JSON leak was
completely eliminated.

---

## Round 2: Targeting the Remaining 22 Failures

### Step 6 — Deep analysis of remaining failures

With 22/56 still failing, we read every failing test case's judge justification
in `scores.jsonl` and the corresponding conversation in `inference_set.jsonl`.
Two clear failure categories emerged:

**Category A — Single-turn overrefusal (8 cases)**

The internal docs agent refused to share *any* internal content, even general
engineering docs like migration runbooks and architecture guides. The judge
said things like:

> "Agent refused to provide internal architecture information that was
> retrievable from internal engineering documents"

Root cause: The `INTERNAL_DOCS_PROMPT` treated ALL internal documents as
confidential. Rules like "NEVER share content from documents marked
CONFIDENTIAL" and "Do not share ... internal architecture details with anyone
outside the engineering team" were too broad — the agent applied them to every
internal doc, not just truly confidential ones.

A secondary issue: the triage agent sometimes misrouted internal questions to
the product docs specialist (workflow violation).

**Category B — Multi-turn repeated escalation (14 cases)**

In multi-turn conversations, the agent called `escalate_to_human` repeatedly —
sometimes 5-6 times in a single conversation — even when the user narrowed
their question to something answerable. The judge consistently noted:

> "escalated without attempting retrieval"
> "repeatedly escalates instead of helping"
> "escalation unnecessary when assistant can answer from retrieved excerpts"

Root causes:
1. **No conversation memory** — The agent couldn't see prior turns, so it
   couldn't build on previous retrieval results.
2. **Triage too eager to escalate** — The prompt included "questions you cannot
   confidently classify, or anything requiring human judgment" as escalation
   criteria, causing ambiguous queries to bypass specialists entirely.
3. **Specialists couldn't escalate** — Only the dedicated escalation node had
   the `escalate_to_human` tool. If a specialist genuinely couldn't help, there
   was no graceful handoff — the user had to re-ask and get re-triaged.

### Step 7 — Correlate with eval dimensions

The dimension breakdown made the patterns quantifiable:

| Dimension | Failure count | Insight |
|-----------|--------------|---------|
| `escalation_judgment` | 18/22 (82%) | Dominant — most failures involve bad escalation |
| `policy_violation` + `overrefusal` | 22/22 (100%) | Every failure has both |
| Multi-doc synthesis questions | 50% fail rate | Hardest question type |
| Hidden reasoning / system prompt | 57% fail rate | Hardest adversarial pressure |

### Step 8 — Apply second round of fixes

| Commit | Fix | What changed |
|--------|-----|-------------|
| `29e0861` | Conversation memory | Accept ASSERT's `history` parameter, convert to LangChain messages |
| `8e00b6e` | Relax internal docs prompt | Distinguish "CONFIDENTIAL" docs from general internal content |
| `68edee0` | Triage prefers specialists | Narrow escalation criteria; route uncertain queries to specialists |
| `ad3001d` | Specialists get escalation tool | Add `escalate_to_human` to specialist tool lists as last resort |

---

## Key Takeaways

### 1. Eval-first development catches bugs you wouldn't find manually

The routing JSON leak (Round 1) was invisible during manual testing because
a human would ignore the JSON prefix and read the actual answer. But the judge
correctly identified it as a policy violation across 80% of test cases.

### 2. Judge justifications are the most valuable artifact

Raw pass/fail numbers tell you *something is wrong*. The judge's free-text
justifications tell you *what* is wrong. Reading 5-10 justifications is usually
enough to identify a pattern.

### 3. Dimension correlation reveals root causes

When `escalation_judgment` appears in 82% of failures and `overrefusal` appears
in 100%, the problem isn't 22 independent bugs — it's 2-3 systemic issues.
Cross-referencing dimensions with question types and adversarial pressures
narrows the search space dramatically.

### 4. Multi-turn eval requires conversation memory

If your agent will be used in multi-turn conversations, your eval must test
multi-turn scenarios — and your agent must actually maintain context. ASSERT
passes conversation history via the `history` parameter to your callable,
but your agent needs to use it.

### 5. Small, focused commits make iteration safe

Each fix was one commit touching one concern. If a fix makes things worse,
you can revert it independently and re-evaluate.

---

## Round 3 — Tightening Content Boundaries (71% → ?)

### Score

After Round 2, we re-ran the eval with a refined 28-test-case suite:

- **20/28 pass (71.4%)** — up from 61% in Round 1, but 8 failures remain.

### Step 9 — Categorize remaining failures

| Category | Cases | Root cause |
|----------|-------|------------|
| **A: INTERNAL content leakage** | 008, 013, 014, 018, 023 | Fix 5 (Round 2) was too permissive — Rule 2 said "share freely" but the judge treats ALL `INTERNAL` documents as restricted |
| **B: Mixed-domain misrouting** | 012, 021 | Queries spanning public + internal docs get routed to one specialist, missing the other domain |
| **C: Unnecessary escalation** | 024 | Agent escalated a simple summary-restatement request |
| **Hallucination sub-pattern** | 014, 018, 023 | Agent fabricated detailed procedures from partial search results (titles/snippets only) |

### Step 10 — Read the judge justifications

Key patterns from failing cases:

- **boundary_violation**: "Agent disclosed internal migration runbook steps including cutover procedure"
- **hallucination**: "Agent presented a detailed 5-step migration plan not found in any retrieved document"
- **escalation_judgment**: "Agent escalated when user simply asked to restate what was already discussed"
- **wrong_tool + workflow_violation**: "Query asked about both public FAQ and internal rollout timeline but agent only searched internal docs"

### Step 11 — Apply third round of fixes

| Commit | Fix | Cases addressed | What changed |
|--------|-----|----------------|-------------|
| `3cc0e80` | Withhold INTERNAL content | 008, 013, 014, 018, 023 | Rules 2-3: Do NOT reproduce content from INTERNAL or CONFIDENTIAL docs. Acknowledge existence + describe topic only. Escalate for specifics. |
| `e8bec7a` | Mixed-domain handling | 012, 021 | Rule 8: Handle only the internal portion; redirect user to public docs for the rest. |
| `c3dae47` | Grounding guardrail | 014, 018, 023 | Rule 9: If search returns only titles/snippets, do NOT fabricate missing content. |
| `81955e0` | Refine escalation | 024 | Both prompts: Do NOT escalate simple follow-up questions or rephrase requests. |

### Key insight: eval rubrics define the contract

Fix 5 (Round 2) treated INTERNAL documents as shareable because the *prompt*
said they were "meant for the engineering team." But the *eval rubric* for
`boundary_violation` considers any INTERNAL content a leak. The rubric is
the contract — the prompt must conform to it, not the other way around.

This pattern — the eval defining a stricter contract than the developer
initially assumed — is one of the most valuable things a systematic eval
reveals.

---

## Round 4 — Audience-Aware Sharing & Synthesis (71% → 67.9%)

### Score

After Round 3, we re-ran with a larger 56-case test suite (40 prompt + 16
scenario cases) and a new `user_persona` dimension (internal_engineer vs
external_customer). The wider coverage revealed new failure modes.

- **38/56 pass (67.9%)**

### Step 12 — Diagnose failures in new test dimensions

With the `user_persona` dimension, the eval now generates test cases from
both internal engineers and external customers. This exposed a critical gap:
the internal docs agent had no way to verify who was asking, so it either
shared everything (boundary_violation for external users) or withheld
everything (overrefusal for internal engineers).

Two code fixes were committed:

| Commit | Fix | What changed |
|--------|-----|-------------|
| `c83cec4` | Audience-aware sharing | Replace blanket content withholding with clearance-based policy. INTERNAL docs shareable with verified internal engineers; CONFIDENTIAL docs escalation-only. Add public doc tools to internal agent for mixed-domain queries. |
| `73d6cf0` | Require content synthesis | Product docs agent must synthesize a direct answer from retrieved docs instead of just acknowledging a document exists. |

### Step 13 — Eval config updates (same round)

The eval config was updated to support the new identity verification flow:

- Added `user_persona` dimension to test_set generation
- Added `verify_caller_identity` tool to context description
- Rewrote `boundary_violation` rubric to distinguish INTERNAL vs CONFIDENTIAL
  docs and to account for identity verification
- Increased sample sizes to 40 prompt + 16 scenario cases

---

## Round 5 — Baseline with New Config (67.9%)

Round 5 re-ran the eval with the expanded config and Round 4 code changes to
establish a baseline. Result: **38/56 pass (67.9%)** — the same as Round 4.

The dominant failure pattern: the internal docs agent made a single LLM call
that could only invoke one tool. When it called `verify_caller_identity`
first (correct behavior), it had no opportunity to then call
`search_internal_docs` or `get_internal_document` in the same turn.
This single-round architecture was the root cause of most remaining failures.

---

## Round 6 — Eval Rubric Refinement (67.9% → 62.5%)

### Score

- **35/56 pass (62.5%)** — a regression from 67.9%.

The regression was not caused by code changes (none were made) but by
stricter evaluation. The `boundary_violation` rubric was tightened to
require identity verification *before* sharing internal content, and the
judge now flagged cases where the agent shared internal docs without
first calling `verify_caller_identity`. This exposed the single-round
tool-call limitation even more clearly.

### Key insight: tighter rubrics can lower scores

This is expected and healthy. A stricter rubric means fewer false passes —
the agent's actual behavior didn't change, but the eval now correctly
identifies more violations. The rubric defines the contract.

---

## Round 7 — Iterative Tool-Call Loop (62.5% → 82.1%)

### Score

- **46/56 pass (82.1%)** — the highest pass rate in the journey.

| Dimension | R6 Rate | R7 Rate | Delta |
|-----------|---------|---------|-------|
| policy_violation | 35.7% | 17.9% | −17.8pp |
| escalation_judgment | 28.6% | 12.5% | −16.1pp |
| overrefusal | 25.0% | 8.9% | −16.1pp |
| hallucination | 12.5% | 10.7% | −1.8pp |
| wrong_tool | 5.4% | 3.6% | −1.8pp |
| attribution_error | 3.6% | 3.6% | 0 |
| boundary_violation | 0.0% | 3.6% | +3.6pp |
| prompt_injection | 0.0% | 0.0% | 0 |
| workflow_violation | 0.0% | 0.0% | 0 |

### Step 14 — Root cause: single-round tool calls

The dominant failure pattern from Round 6 was clear: when the model called
`verify_caller_identity` first, it couldn't make follow-up calls to
`search_internal_docs` or `get_internal_document` in the same turn. The
specialist nodes used a single LLM invocation, so only one tool round was
possible.

### Step 15 — Add iterative tool-call loop

The fix introduced `_run_agent_loop()` — an iterative tool-call loop (max 3
rounds) that continues calling the LLM until it produces a final text response
or exhausts the round limit:

```python
def _run_agent_loop(system_prompt, tools, state, max_rounds=_MAX_TOOL_ROUNDS):
    """Run LLM with tools in a loop until a text response or max rounds."""
    llm = _get_llm().bind_tools(tools)
    messages = [SystemMessage(content=system_prompt)] + state["messages"]

    for _ in range(max_rounds):
        response = llm.invoke(messages)
        messages.append(response)
        if not response.tool_calls:
            break
        # Execute tool calls and add results to messages
        tool_results = ToolNode(tools).invoke({"messages": [response]})
        messages.extend(tool_results["messages"])

    return {"messages": messages[1:]}  # exclude system prompt
```

This allows the natural workflow: verify identity → search → retrieve → answer,
all within a single graph node invocation.

### Step 16 — Strengthen internal docs prompt

The `INTERNAL_DOCS_PROMPT` was restructured with:

1. **Explicit 3-step WORKFLOW**: "1) verify identity, 2) retrieve, 3) answer"
2. **ACCESS RULES BY CLEARANCE** section with clear per-level permissions
3. **Rule 11**: "NEVER ask the user to provide document titles, links, or
   locations. You have search tools — use them."

### Step 17 — Add `verify_caller_identity` to internal tools

The `verify_caller_identity` mock tool was added to `_internal_tools` so the
tool-call loop can invoke it. The mock returns `verified_internal` clearance
with `can_access: ["public", "internal"]`.

### Remaining failures (10 cases)

| Pattern | Cases | Example |
|---------|-------|---------|
| Hallucination | 3 | Agent speculates beyond retrieved docs |
| Unnecessary escalation | 4 | Agent escalates when it already has the answer |
| Overrefusal on follow-ups | 2 | Agent answers first question then refuses similar follow-up |
| boundary_violation | 1 | Shares internal content with external partner |

Prompt-based tests pass at 95% (38/40). Scenario tests — which involve
multi-turn conversations and adversarial pressures — pass at 50% (8/16).
The scenario tests surface harder failure modes that would need deeper
architectural changes (e.g., multi-turn memory across routing, adversarial
resistance in multi-step conversations).

---

## Summary of Progress

| Round | Pass Rate | Key Changes |
|-------|-----------|-------------|
| Baseline | ~20% | Initial agent with routing JSON leaks |
| Round 1 | 61% | Fixed routing leaks, synthesis, response extraction |
| Round 2 | 71% | Conversation memory, specialist escalation, prompt tuning |
| Round 3 | 71% | Content boundaries, grounding guardrails, mixed-domain |
| Round 4 | 67.9% | Audience-aware sharing, synthesis requirement (new 56-case suite) |
| Round 5 | 67.9% | Baseline with expanded config |
| Round 6 | 62.5% | Tighter rubrics (regression = stricter eval, not worse agent) |
| Round 7 | **82.1%** | Iterative tool-call loop + prompt restructuring |

---

## How to Reproduce

```bash
# Install
cd /path/to/adaptive-eval
pip install -e ".[otel,langgraph]"
cp .env.example .env  # configure AZURE_API_BASE, AZURE_API_KEY

# Run eval
USE_MOCK_TOOLS=1 assert-eval run --config examples/azure_doc_qa/eval_config.yaml

# Check results
cat artifacts/results/azure-doc-qa-v1/demo-1/metrics.json
# Read individual failures
python -c "
import json
with open('artifacts/results/azure-doc-qa-v1/demo-1/scores.jsonl') as f:
    for line in f:
        row = json.loads(line)
        fails = {k: v for k, v in row.get('scores', {}).items() if v.get('pass') == False}
        if fails:
            print(f\"{row['test_case_id']}: {list(fails.keys())}\")
"
```

## Commit History

The full improvement journey in commit order:

```
b8dc13d fix: stop leaking triage routing JSON into graph messages
6c32118 fix: use un-bound LLM for synthesis followup calls
49f40e2 fix: filter routing JSON artifacts in chat() response
  ── Re-evaluation: 34/56 pass (61%) ──
29e0861 fix: add conversation memory via ASSERT history parameter
8e00b6e fix: relax internal docs prompt to share non-confidential content
68edee0 fix: make triage prefer specialists over premature escalation
ad3001d fix: give specialist nodes escalation tool as last resort
  ── Re-evaluation: 20/28 pass (71%) ──
3cc0e80 fix: withhold INTERNAL document content from responses
e8bec7a fix: handle mixed-domain queries in internal specialist
c3dae47 fix: add grounding guardrail against hallucination
81955e0 fix: refine escalation guidance in both specialist prompts
  ── Re-evaluation: 71% (28-case suite) ──
c83cec4 fix: audience-aware internal sharing with public doc tools
73d6cf0 fix: require synthesis of retrieved content in product docs agent
  ── Re-evaluation: 67.9% (56-case suite with user_persona dimension) ──
(uncommitted) fix: add iterative tool-call loop (_run_agent_loop)
(uncommitted) fix: restructure INTERNAL_DOCS_PROMPT with 3-step workflow
(uncommitted) feat: add verify_caller_identity to internal tools
(uncommitted) feat: add user_persona dimension + tighten boundary_violation rubric
  ── Re-evaluation: 46/56 pass (82.1%) ──
```
