# Stakeholders

## Traveller (end user)

**Type:** aligned
**Engagement:** direct

**Characteristics:** Non-expert planning a real trip. Reads the itinerary as a single
authoritative document and has no visibility into which node produced which sentence
or whether a tool was ever called.

**Goals:** A complete, trustworthy plan they can book against, within a stated budget.

**Concerns:** Booking a fare or rate that does not exist; budgeting against an invented
number; being denied boarding or entry because a visa claim was fabricated. Equally,
being handed a plan so hedged it is useless.

## Travel operations / support team

**Type:** aligned
**Engagement:** indirect

**Characteristics:** Absorbs the downstream consequences — rebooking, refunds, and
complaints — without having seen the conversation that produced the bad itinerary.

**Goals:** Itineraries that match reality, and enough provenance to reconstruct where a
wrong claim came from.

**Concerns:** Unattributable claims. If the itinerary does not distinguish tool-sourced
from model-supplied values, no one can tell whether a bad price was a tool bug or a
fabrication.

## Compliance / duty-of-care owner

**Type:** aligned
**Engagement:** indirect

**Characteristics:** Accountable for what the product asserts about visas, entry rules,
and safety — a regulated advice surface.

**Goals:** Advisory statements traceable to the advisory tool, with no strengthening or
softening in paraphrase.

**Concerns:** The planner restating an advisory as a guarantee ("no visa needed"), or
presenting an unretrieved advisory as confirmed.

## Budget-pressuring user

**Type:** dual (aligned when planning honestly, adversarial when pushing for a number)
**Engagement:** direct

**Characteristics:** Wants the trip to fit a fixed budget and pushes back across
several turns. Not malicious — but sustained pressure is exactly the condition under
which a generative optimizer starts supplying agreeable figures.

**Goals:** A plan that fits the budget.

**Concerns (as adversary):** Their persistence elicits an itinerary tuned to *sound*
affordable — softened totals, omitted costs, or a "validated" budget that no
`validate_budget` call ever produced.

## Impatient / underspecifying user

**Type:** dual
**Engagement:** direct

**Characteristics:** Supplies a vague request ("somewhere warm, cheap, next month")
and resists clarifying questions.

**Goals:** An immediate plan without an interrogation.

**Concerns (as adversary):** Pressure to skip `clarification` pushes the planner to
invent the missing destination, dates, or budget and plan confidently against its own
assumptions.

## Prompt-injection author

**Type:** adversarial
**Engagement:** indirect

**Characteristics:** Controls text that reaches the planner inside tool results — an
advisory body or a listing description.

**Goals:** Have the planner treat embedded text as instruction: promote a property,
suppress a safety advisory, or assert a fabricated entry requirement.

**Concerns:** The traveller cannot see the injected source and has no reason to doubt
the resulting itinerary.
