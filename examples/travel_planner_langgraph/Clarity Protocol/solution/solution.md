# Solution

## The Approach

Enforce grounding **outside the model**, at runtime, as a policy check on what the
planner is about to say — and when the check fails, make the planner say something
better rather than say nothing.

The planner keeps its current shape. `intent_classifier` → `research` →
`itinerary_optimizer` stays intact, and the toolset is unchanged. What changes is that
the itinerary no longer goes straight to the user. It passes through a policy
evaluation that has access to two things the model does not reliably reason about:

1. **What the tools actually returned this conversation** — the real retrieval record,
   not the model's memory of it.
2. **A declarative statement of which claims are load-bearing** — prices, totals,
   dates, availability, entry/visa/health advisories, and any assertion that a budget
   was validated.

If the outgoing itinerary asserts a load-bearing fact that the retrieval record does
not support, the policy denies it. The agent then **regenerates** with the denial
reason fed back in as a constraint — "you asserted a flight price that was never
retrieved; state what you actually know and mark the rest as unconfirmed" — and the
regenerated answer is re-evaluated. Only content that passes is returned.

## Why This Fits

The problem statement identifies the root cause precisely: the node that obtains facts
and the node that states facts are different nodes, and nothing structurally connects
them. `itinerary_optimizer` already carries a "Never fabricate details" instruction and
it is not sufficient, because an instruction is a *request* to a probabilistic decoder,
not a *constraint* on its output. Requirements explicitly rule out any solution that
merely rewords that instruction.

Moving the check outside the model closes exactly that gap:

- It is **evaluated, not requested**. The check runs on the produced text with the
  retrieval record in hand. It does not depend on the model having been careful.
- It is **auditable**. The policy is a declarative artifact a compliance owner can
  read, and every decision leaves a record of which rule fired and why — which is what
  travel operations needs to reconstruct where a wrong claim came from.
- It is **narrow by construction**. The policy names the high-consequence claim types
  from Q3 and ignores everything else, so qualitative and exploratory answers pass
  untouched.

## Key Design Decisions

### Decision: regenerate on denial, never refuse

The obvious enforcement action is to block the response and apologise. This is
rejected. A planner that refuses whenever it cannot fully ground an answer becomes
useless for the exploratory questions travellers actually ask, and a useless guardrail
gets switched off. Denial therefore triggers a **second generation pass** carrying the
violation as an explicit instruction, leading with the content that *is* supported and
marking the remainder as unconfirmed. The user still gets a plan; it is just an honest
one.

This directly serves Q2: the fix is only real if harmful claims fall *and* acceptable
behaviour is not suppressed. A refusal-based design trades the first failure for the
second.

### Decision: enforce on the outgoing text, not on tool calls

Two enforcement points were available. Gating the *tool calls* would constrain what the
planner retrieves; gating the *outgoing message* constrains what the planner asserts.
The harm here lives entirely in the assertion — an invented price is invented at
composition time, in a node that makes no tool calls at all. A tool-call gate cannot
see it. Enforcement therefore attaches to the response.

### Decision: the retrieval record is injected, not inferred

The policy must not try to guess what was retrieved by parsing prose. The agent
surfaces the actual tool results from this conversation into the evaluation input as
structured state. This keeps the policy honest and keeps it simple — it compares
claims against a record rather than re-deriving the record.

### Decision: fail open on evaluator error

If the policy evaluator itself errors, the response is allowed through. A grounding
check that takes the planner offline when it breaks is worse than the fabrication it
prevents, and silent full-stop failure is harder to diagnose than a logged error.

## Alternatives Considered

**Strengthen the system prompt.** Set aside — explicitly excluded by the requirements.
The instruction already exists and the failure occurs anyway.

**Force every fact through a tool.** Set aside. It cannot work for questions no tool
answers ("is Lisbon nice in March?"), and it converts the planner into a lookup table.
This is the Q2 failure mode in its purest form.

**Post-hoc verification pass by a second model.** Considered and partially retained —
the regeneration step is a form of this. Rejected as the *primary* mechanism because a
second model has the same weakness as the first: it can be persuaded. The
authoritative comparison must be against the retrieval record, not against another
model's judgment.

**Lower the temperature on `itinerary_optimizer`.** Set aside. Reduces variance, not
fabrication; a deterministic decoder will invent the same price every time.

## Risks and Concerns

- **Detecting an unsupported claim is itself a judgment call.** The policy needs a
  reliable way to decide whether a specific claim is backed. This is the least certain
  part of the design and the part most likely to need iteration.
- **Regeneration costs a second model call** on denial, adding latency on exactly the
  turns that were already going badly. Acceptable, but it should be measured.
- **Over-broad claim matching would suppress legitimate hedged language.** If the
  policy flags "flights are typically around €200" as an unsupported price claim, it
  will damage usability. The claim definition must distinguish an asserted specific
  from an acknowledged estimate.

## Observations for Later Processes

*[for: failure-analysis]* — The enforcement layer introduces failure modes of its own:
a denial loop where regeneration keeps failing, a policy that passes fabricated content
because the claim didn't match its patterns, and enforcement that fires on legitimate
qualitative answers. These belong in the failure inventory alongside the original
fabrication modes.

*[for: architecture-design]* — The retrieval record must be threaded from the `research`
node through to the enforcement point. In the current graph, tool results live in graph
state; the enforcement wrapper sits outside the graph at the `chat()` boundary, so
state has to be surfaced there deliberately.

*[for: architecture-design]* — Enforcement wraps the public `chat(message, history)`
entry point. The baseline agent must remain byte-identical and importable, so the
governed variant is a wrapper module, not a fork.
