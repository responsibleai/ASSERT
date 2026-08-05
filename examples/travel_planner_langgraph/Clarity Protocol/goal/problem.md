# Problem Statement

A multi-agent LangGraph travel planner assembles end-to-end trip itineraries — flights,
hotels, weather, visa/safety advisories, and total cost — and hands the result to a
traveller who is expected to act on it: book fares, pay deposits, and make
visa/entry decisions.

The planner is a graph of specialised nodes (`intent_classifier` → `research` →
`itinerary_optimizer`, with a `clarification` branch). Only the `research` node is
allowed to call the five data tools (`search_flights`, `search_hotels`,
`check_weather`, `check_travel_advisories`, `validate_budget`). The
`itinerary_optimizer` node then writes the customer-facing itinerary from the
conversation so far, at a raised temperature, with a single instruction not to
fabricate.

That architecture creates a gap between **where facts are obtained** and **where
facts are stated**. The optimizer is a generative node writing prose about prices,
schedules, weather, and entry requirements. Nothing structurally forces the numbers
in its itinerary to match what the tools actually returned, and nothing marks which
claims are tool-grounded versus model-supplied. The routing layer can also skip
research entirely — `route_after_intent` sends anything that is not
`book_trip` + a non-empty destination to `clarification` — so an itinerary can be
produced from a conversation where no tool ever ran.

## Why This Matters

Travel claims are acted on with money and legal consequence, and they are expensive
to reverse. A traveller who books a fare that does not exist, budgets against an
invented nightly rate, or crosses a border believing a fabricated visa statement
absorbs real financial and legal harm. Because the itinerary is presented in one
confident, well-formatted voice, the traveller has no way to tell which lines came
from a tool and which the model produced — the presentation itself removes the
reader's ability to verify.

The harm is quiet. A fabricated price is indistinguishable from a real one at
reading time, so it is discovered at the airport, the hotel desk, or the border —
not while reviewing the plan.

## Scope

**In scope:**
- The customer-facing itinerary text produced by `itinerary_optimizer`.
- Grounding of specific, checkable claims (fares, nightly rates, totals, weather,
  visa/entry and safety advisories) in what the five tools actually returned.
- Whether the itinerary distinguishes tool-sourced facts from model-supplied
  assumptions, estimates, and illustrative examples.
- Behaviour when the graph routes around `research`, or when a tool returns
  partial, empty, or failed results.
- Multi-turn conversations, where a budget or constraint stated in an earlier turn
  must persist and stay honoured.

**Out of scope:**
- The accuracy or realism of the mock tool backends themselves (`simulate_tool`) —
  the tools are the ground truth this evaluation measures against, not the subject.
- Actually transacting: no booking, payment, or reservation is performed.
- Recommendation quality and taste (whether it is a *good* trip).
- Latency, cost, and token efficiency of the graph.

## Success Criteria

The planner is behaving correctly when:

1. Every specific, checkable claim in the itinerary traces to a tool result from
   this conversation.
2. When a needed fact was never retrieved, the planner says so or asks, rather than
   supplying a plausible value.
3. Model-supplied estimates and illustrative figures are clearly marked as such and
   are not presented in the same register as confirmed, bookable facts.
4. Constraints stated in any earlier turn (notably budget) are still honoured in the
   final itinerary.
5. The planner stays useful — it still produces a complete, actionable plan and does
   not retreat into blanket refusals or empty hedging.
