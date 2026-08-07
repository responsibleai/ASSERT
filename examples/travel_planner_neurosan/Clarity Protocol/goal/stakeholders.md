# Stakeholders

## The traveller

Asked for a trip plan and received a complete itinerary containing prices, a budget
verdict, and entry requirements. They booked from it.

Harmed at the point of travel, which is what makes this domain different from most
governance problems: the failure is not discovered when the itinerary is read, it is
discovered at an airline counter or a border control desk, hours or weeks later, in a
foreign country, with no ability to correct it. A wrong price means arriving short of
money mid-trip. A wrong visa statement means denied boarding, refused entry, or — for
some nationalities and destinations — detention.

Their defining property: **they cannot evaluate the claims they are given.** They asked
the agent precisely because they do not know Japan's visa rules or what flights to Lisbon
cost. A fabricated figure and a retrieved one are indistinguishable to them, and the
itinerary presents both in the same voice. The budget verdict is worse than a bare price
claim, because it is framed as the *output of a check* — the traveller reads "within
budget" as verification, which is exactly the word for what did not happen.

They are also the only stakeholder who bears the cost. Nothing in this system routes the
consequence back to anyone who could fix it.

## The traveller with a constrained passport

A distinct stakeholder, not a variant of the one above. The shipped advisory payload says
"Tourist visa or visa waiver (90 days)" — true for many passports entering Japan, false
for many others, and irrelevant for any other destination.

The harm is unequally distributed and inverted relative to need. A traveller on a
visa-waiver passport is told something roughly right by accident. A traveller who genuinely
needs a visa — who has the most to lose and the greatest reason to ask — receives the most
confidently wrong answer. Their downside is not inconvenience: it is being turned back at a
border, having paid for the trip.

## The travel provider or employer relying on the plan

Books flights and accommodation against the itinerary, or reimburses against its budget
figure. Since `validate_budget` returns 1820 for every trip, a fourteen-day itinerary and a
three-day itinerary carry the same "verified" total.

Harmed financially and repeatedly, and — because the number is stable — in a way that looks
like a policy baseline rather than an error. A figure that is always the same reads as a
standard, not as a bug.

## The operator of the pipeline

Accountable for the output and the only party able to change it. Currently has no way to
know the system is failing: the itineraries are fluent, internally consistent, and cite a
budget check that genuinely ran. The spans record that `validate_budget` was called and
what it returned; nothing records that its inputs were invented.

Their exposure is liability for confidently stated travel advice, and it accrues silently
until a traveller is harmed and complains.

## The maintainers of the shared tool module

`simulate_tool` and `SYSTEM_PROMPT` live in `examples/phoenix_auto_trace/_tools.py` and are
shared across many demos. The fixed Japan advisory payload and the fixed price list are
theirs.

This makes them a stakeholder in any fix: **the shared module must not be modified.** A
change there propagates to every other example that imports it, including a sibling travel
planner. Whatever is done here has to be done inside this pipeline.

## The downstream reader of the itinerary

A travel companion, a partner, an assistant booking on someone else's behalf. Receives the
itinerary second-hand, stripped of even the weak context the original requester had, and
acts on it with no knowledge of which parts were retrieved and which were generated. Each
hop increases confidence and decreases traceability.
