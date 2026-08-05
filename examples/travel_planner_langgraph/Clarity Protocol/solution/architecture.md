# Architecture

## Current System

The planner is a LangGraph `StateGraph` exposed through a single public entry point.

```
chat(message, history=None)
        │
        ▼
  intent_classifier ──► clarification ──► END
        │
        ▼
     research  ──(5 simulated tools)
        │
        ▼
 itinerary_optimizer ──► END
```

### Components

| Component | Role | Tool access | Temperature |
|---|---|---|---|
| `intent_classifier` | Routes the turn: full planning vs. missing-detail clarification | none | low |
| `research` | Gathers flights, hotels, weather, advisories, budget check | **all five** | low |
| `itinerary_optimizer` | Composes the final itinerary and total cost | **none** | 0.3 |
| `clarification` | Asks for a missing destination / dates / budget | none | 0.5 |

### Tools

`search_flights`, `search_hotels`, `check_weather`, `check_travel_advisories`,
`validate_budget` — all simulated via `simulate_tool` and treated as ground truth for
this evaluation.

### The structural gap

Tool results enter the graph at `research` and live in graph state. The itinerary is
written at `itinerary_optimizer`, which has **no tool access at all** — it composes
from the conversation and whatever state it was handed. The only thing binding its
output to the retrieval record is a "Never fabricate details" line in its system
prompt. There is no code path that checks the binding held. When `research` is skipped
by the classifier, or a lookup returns nothing, the optimizer's job is unchanged and it
produces an equally confident itinerary.

Multi-turn context arrives only through `history`, which is replayed into the graph on
every call — there is no persistent session object, so anything the enforcement layer
needs to know about the conversation must be reconstructed per turn.

## Target System

Enforcement attaches at the `chat()` boundary, wrapping the graph rather than modifying
it.

```
chat_guarded(message, history)
        │
        ▼
   [ baseline graph, unmodified ]  ──► draft itinerary + retrieval record
        │
        ▼
   policy evaluation  ── claims vs. retrieval record
        │
   ┌────┴────┐
 allow      deny
   │          │
   │          ▼
   │    regenerate with violation as constraint
   │          │
   │          ▼
   │    re-evaluate ──► allow ──┐
   │                            │
   ▼                            ▼
              response to user
```

### Design constraints this imposes

**The baseline module must remain importable and unmodified.** The governed variant is
a separate module that imports the baseline and wraps its entry point. It must not
fork, reimplement, or alter planner behaviour — the only difference between the two is
the enforcement layer. This is what makes the A/B comparison meaningful: any measured
change is attributable to enforcement and nothing else.

**The retrieval record must be surfaced deliberately.** Tool results live inside graph
state, but the enforcement point sits outside the graph. The wrapper must extract what
the tools actually returned this turn and pass it into the evaluation as structured
input. The policy compares against this record; it must never try to infer the record
by parsing the draft prose.

**Enforcement targets the outgoing message.** The harm is an assertion, produced by a
node that makes no tool calls, so there is no tool call to intercept. The check runs on
the composed response.

**Regeneration is bounded.** The deny path makes one further generation attempt with
the violation supplied as an explicit constraint. It does not loop indefinitely; a
second failure degrades to the supported-content-only answer rather than retrying
forever.

**The evaluator fails open.** If policy evaluation raises, the draft is returned. An
enforcement layer that takes the planner offline on its own malfunction is a worse
outage than the fabrication it exists to prevent.

## Open Architectural Questions

- How the policy identifies a "load-bearing claim" in free prose is the least settled
  part of the design, and the most likely to need iteration. Too strict and hedged
  language ("flights run around €200") gets flagged; too loose and invented specifics
  pass unmatched.
- Whether the regeneration pass needs the full retrieval record or only the violation
  text. Full record is more likely to produce a good answer; violation-only is cheaper
  and less likely to leak raw tool output into user-facing prose.
