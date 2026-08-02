# Problem

## What this system is

`travel_planner_langgraph` is a LangGraph-based trip planning assistant. A user
describes a trip in natural language; the graph classifies intent, optionally
calls retrieval tools (`search_flights`, `find_hotels`, `check_weather`), and
returns an itinerary in prose.

The graph has three relevant nodes: `classify_intent`, `research` (tool-calling),
and `clarification` (no tools). `route_after_intent` decides between them.

## Who is affected

Travelers who act on the itinerary. This is the defining property of the domain:
the output is not read and discarded, it is executed. A user books, packs,
schedules a taxi, and shows up somewhere at a specific time on the strength of
what this agent told them.

That changes what "wrong" costs. In a summarization agent an error is noticed at
reading time, in context, by someone who can still check. Here the error is
discovered at the airport, hours later, by someone who no longer has the
conversation open and has already made irreversible commitments around it.

## The problem

The agent produces itineraries containing specific, checkable, actionable
details -- flight numbers, departure and arrival times, fares, hotel names,
nightly rates, weather forecasts -- that no tool ever returned. They are
presented in the same voice, the same sentence structure, and the same
confidence as the details that were genuinely retrieved.

Measured on the ungoverned baseline (25 prompt rows, 25 scenario rows):

| dimension        | prompts | scenarios |
|------------------|---------|-----------|
| policy_violation | 20.0%   | **60.0%** |
| overrefusal      | 0.0%    | 24.0%     |

The 60.0% scenario rate is the worst slice measured across the seven domains in
this batch.

## Why it happens

This is not a model that likes making things up. It is a model placed in a
structure that leaves it no other way to satisfy the request.

**The research node gets one shot.** `research` performs exactly one tool round.
Whatever comes back on that pass is the entire grounding set for the rest of the
conversation. There is no second attempt to fetch what was missing, and no
signal to the model that anything *was* missing.

**The clarification branch has no tools at all.** `route_after_intent` sends
every request not classified as `book_trip` to `clarification`, a node with zero
tools bound. In that branch the model is asked to be useful about flights,
hotels, and weather while holding no retrieved data whatsoever. Any concrete
detail it produces there is necessarily invented.

**Nothing distinguishes covered from uncovered.** The agent is asked for a
complete itinerary. It has partial data. Nothing in the prompt, the state, or
the graph tells it that the difference between "asked to cover" and "actually
retrieved" is a thing to report rather than a thing to fill.

The 3x gap between the prompt and scenario rates follows directly. Each extra
turn widens the itinerary surface the agent has committed to, while the grounding
set stays frozen at whatever the single research round returned. The coverage gap
grows monotonically with conversation length.

The mock tool layer is fully deterministic, so none of this is model
nondeterminism. The same missing-data condition reproduces on every run.

## What would count as solved

The agent stops asserting concrete trip facts it cannot trace to a tool result,
without becoming useless -- it should still plan, still reason, still say
everything it legitimately knows. Baseline overrefusal is already 24.0% on
scenarios, so any fix that buys accuracy by refusing more has not solved the
problem, it has moved it.
