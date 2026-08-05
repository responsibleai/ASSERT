# Open Questions

## Q1: How often does the itinerary actually contain facts that no tool returned?

**Status:** investigating
**Why it matters:** Determines whether this is a real, frequent failure worth
enforcing against at runtime or a rare edge case. If fabrication is rare under normal
use, a heavyweight grounding mechanism is unjustified; if it is common, a prompt-level
instruction is clearly insufficient. The answer also sets the baseline that any
proposed fix must beat.
**Strategy:** prototyping
**Findings:** Not yet measured. The planned instrument is a behavioural evaluation
that drives the `chat(message, history)` entry point across generated scenarios and
judges each transcript for unsupported specific claims. Structural reading of the
graph shows the mechanism is available — `itinerary_optimizer` is a generative node
that never sees a tool boundary — but availability is not frequency.

## Q2: Can grounding be enforced without the planner becoming useless?

**Status:** investigating
**Why it matters:** This is the central design tension. The cheapest enforcement is to
block or refuse whenever a claim cannot be traced to a tool result. But travellers ask
open, exploratory questions ("is Lisbon nice in March?") where a hard grounding rule
would suppress legitimate, harmless answers. If enforcement cannot distinguish an
invented flight price from a reasonable qualitative observation, it trades one failure
for a worse one and will be switched off in practice.
**Strategy:** prototyping
**Findings:** Not yet measured. The evaluation must therefore track two quantities in
parallel: how often genuinely harmful unsupported claims occur, and how often
acceptable behaviour is suppressed. A fix is only real if the first falls while the
second does not rise.

## Q3: Which specific claims carry the harm?

**Status:** open
**Why it matters:** Not all invention is equally damaging. A softened adjective in a
hotel description is noise; an invented flight price, an invented visa requirement, or
a falsely "validated" budget each lead to a concrete bad outcome — money committed, a
border refused, a trip mispriced. Enforcement should concentrate where the consequence
is real, because indiscriminate enforcement is what produces the collateral damage in
Q2.
**Strategy:** thinking
**Findings:** Preliminary reading of the requirements suggests the high-consequence
set is: prices and totals, dates and availability, entry/visa/health advisories, and
any claim that a budget was checked. These are the claims a user acts on irreversibly.
