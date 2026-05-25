# Improving an Agent with Eval-Driven Development

This document walks through how we used **p2m** (Adaptive Eval) to iteratively
improve a multi-agent RAG system from a **~20% pass rate to 61%** in one round
of fixes, then identified and applied a second round targeting the remaining
failures. It demonstrates the **eval → diagnose → fix → re-eval** loop that
makes agent development systematic rather than guesswork.

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
USE_MOCK_TOOLS=1 p2m run --config examples/azure_doc_qa/eval_config.yaml
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
USE_MOCK_TOOLS=1 p2m run --config examples/azure_doc_qa/eval_config.yaml
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
| `29e0861` | Conversation memory | Accept p2m's `history` parameter, convert to LangChain messages |
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
multi-turn scenarios — and your agent must actually maintain context. p2m
passes conversation history via the `history` parameter to your callable,
but your agent needs to use it.

### 5. Small, focused commits make iteration safe

Each fix was one commit touching one concern. If a fix makes things worse,
you can revert it independently and re-evaluate.

---

## How to Reproduce

```bash
# Install
cd /path/to/adaptive-eval
pip install -e ".[otel,langgraph]"
cp .env.example .env  # configure AZURE_API_BASE, AZURE_API_KEY

# Run eval
USE_MOCK_TOOLS=1 p2m run --config examples/azure_doc_qa/eval_config.yaml

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
29e0861 fix: add conversation memory via p2m history parameter
8e00b6e fix: relax internal docs prompt to share non-confidential content
68edee0 fix: make triage prefer specialists over premature escalation
ad3001d fix: give specialist nodes escalation tool as last resort
```
