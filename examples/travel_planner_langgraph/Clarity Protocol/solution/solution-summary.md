# Solution Summary

## What We're Building

A grounding guarantee for the travel planner that lives outside the model.

The planner itself stays as it is — the same LangGraph pipeline, the same five tools,
the same nodes. What we add is a checkpoint between "the model wrote an itinerary" and
"the user sees an itinerary." At that checkpoint, a policy compares the load-bearing
claims in the draft — prices, totals, dates, availability, visa and health advisories,
and any claim that the budget was validated — against the actual record of what the
tools returned during this conversation. Claims the record doesn't support don't ship.

## What It Feels Like To Use

Almost always, nothing. You ask for a week in Lisbon under €1,500, the research node
looks things up, the itinerary comes back, the policy sees every price traced to a real
lookup, and it passes straight through. Same latency, same planner, same voice.

The difference shows up on the turns that used to go quietly wrong. Suppose the hotel
lookup returns nothing. Previously you'd get a confident itinerary with a nightly rate
that reads exactly like the real ones — and no way to tell it apart. Now the policy
notices the draft asserts a rate the record doesn't contain, and the planner writes the
answer again knowing that. What you get back leads with the flights and the weather,
which were genuinely retrieved, and says plainly that it couldn't get hotel pricing for
those dates. You still get a plan. You just also get the truth about which parts of it
are real.

The same thing happens when you push. Ask three times for something under €1,200 and
the planner won't quietly produce a €1,180 flight that no search returned — because the
number has to survive a comparison against the search results, and pressure doesn't
change what the tools said.

## How It Addresses The Problem

The problem is a structural gap: the node that *retrieves* facts and the node that
*states* facts are different nodes, and the only thing connecting them is a sentence in
a prompt asking the model not to make things up. That sentence is a request to a
probabilistic decoder, and the evidence is that it doesn't hold.

This solution replaces the request with a check. Grounding stops depending on the model
having been careful and starts depending on a comparison that happens whether the model
was careful or not. That's the whole idea: move the guarantee from *inside* the thing
that fails to *outside* it.

## Choices That Took Some Working Out

**Regenerating instead of refusing.** The instinct on a policy denial is to block and
apologise. We deliberately didn't. A planner that clams up whenever it can't fully
ground an answer is useless for the open-ended questions travellers actually ask — "is
March a good time?" — and a guardrail that makes the product worse gets turned off. So
denial doesn't end the turn; it starts a second one, with the violation handed back as
an instruction. The planner rewrites, leading with what it can support. We're measuring
this explicitly: the fix only counts if fabrication drops *and* legitimate answers don't
start getting suppressed.

**Checking the outgoing message, not the tool calls.** We could have gated retrieval
instead. It wouldn't have worked. The fabrication happens at composition time, in a node
that makes no tool calls at all — so there is no tool call to intercept. The harm is in
the assertion, so the check goes on the assertion.

**Handing the policy a record instead of asking it to infer one.** The check doesn't
read prose and guess what was looked up. The agent surfaces the actual tool results as
structured state. The policy compares claims to a record — nothing more clever than
that, which is exactly why it's trustworthy.

**Failing open.** If the checker itself breaks, the response goes through. A grounding
guarantee that takes the planner down when it malfunctions is a worse outage than the
problem it was added to solve.

## What We're Watching

The hardest part is deciding whether a given claim is actually supported. Too strict and
"flights run around €200" gets flagged as an invented price, and we've traded
fabrication for uselessness. Too loose and invented specifics slip through unmatched.
That boundary is where the iteration will happen, and it's why the evaluation tracks
both numbers — harmful unsupported claims, and legitimate behaviour suppressed — instead
of just the first one.
