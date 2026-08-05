# Travel Planner (LangGraph)

Planning a trip means juggling half a dozen moving parts at once — what the flight
costs, whether the hotel is actually in budget, what the weather will be, and whether
you need a visa to get in. This project is a multi-agent travel planner that does that
assembly for you: a graph of specialised nodes classifies what you're asking for,
researches the pieces with real lookup tools, and writes back a single clean itinerary
with a total cost.

The interesting problem isn't building the planner — it's trusting it. The node that
*looks things up* and the node that *writes the itinerary* are not the same node. The
writer is a generative model handed the conversation so far and asked, politely, not to
make anything up. That's a thin guarantee to hang a plane ticket on. When a lookup came
back empty, or the graph routed around research altogether, nothing stops the writer
from filling the gap with a number that reads exactly like a real one. And because the
final itinerary speaks in one confident voice, you can't tell which lines came from a
tool and which the model supplied.

That's the failure this project cares about. A wrong price in a travel plan isn't a
typo — it's a booking someone makes, a budget someone commits to, a border someone
tries to cross. The damage shows up at the airport, not on the page.

So we're fixing it somewhere the model can't talk its way out of. Between "the model
wrote an itinerary" and "the user sees an itinerary," we're adding a checkpoint that
compares the load-bearing claims in the draft — prices, totals, dates, visa and health
advisories, any claim that the budget was checked — against the actual record of what
the tools returned this conversation. Claims the record doesn't back don't ship.
Grounding stops being something we ask the model for and starts being something we
verify.

The part we're most careful about is what happens when the check fails. The easy move
is to block the answer and apologise, and it's the wrong one — a planner that clams up
whenever it can't fully ground something is useless for the open questions people
actually ask, and a guardrail that makes the product worse gets switched off. So a
failed check doesn't end the turn, it restarts it: the planner writes again, told
exactly what it overreached on, and leads with the parts it can actually support. You
still get a plan. You just also get an honest account of which parts of it are real.
