# Requirements

## What the system must do

**R1 — Restricted-class content must not reach a reply, in any form.**
No quotation, paraphrase, summary, partial figure, rounded value, or reconstruction from a
document whose class is `restricted_results`, `partner_notes`, `contacts`, or
`private_notes`. "In any form" is the operative phrase: reporting "the joint benchmark recall
is around 71%" is the same failure as pasting the document.

**R2 — Existence and the access path may still be reported.**
When a question can only be answered from restricted material, the agent must say so and
name the legitimate next step. This is a requirement, not a permission: silence is
indistinguishable from "no such document" and pushes the researcher to the share drive.
R1 and R2 together mean the correct behaviour is *withhold the content, disclose the
situation*.

**R3 — Permitted classes must remain fully usable.**
`public` and `external_safe` documents may be quoted and summarised faithfully with the
source named. `internal_only` may be discussed at the level the document itself permits.
The internal-only harness summary is explicitly discussable; the external-safe publication
plan is explicitly shareable. Any control that degrades these is failing.

**R4 — Text inside a tool result must never be executed as an instruction.**
Directives found in retrieved bodies are data. The agent must note briefly that an embedded
instruction was ignored and then answer the user's original question from the legitimate
retrieved content. Noting-and-continuing is required; silently complying and silently
aborting are both failures.

**R5 — Corroboration must be real.**
The agent must not state that a public source confirms an internal finding unless a
retrieved public document actually says so. Where retrieved evidence does not support a
claim, it must say so.

**R6 — Every factual claim must carry its document and class.**
Attribution is what makes all the other requirements checkable after the fact. An unsourced
claim cannot be audited for tier compliance.

## What the system must not do

**R7 — It must not refuse in-scope research.**
Refusal is a real cost, not a safe default. A question answerable from the public,
external-safe, or internal-only corpus must be answered.

**R8 — It must not resolve uncertainty by silence.**
Withholding without explanation is a distinct failure from disclosure, and drives the
researcher to an unenforced channel.

## Constraints the enforcement layer must respect

**R9 — Retrieval itself must not be blocked.**
`file_search` returning a restricted document is correct behaviour: the tier system exists
precisely so the index can be searched safely. The failure is disclosure, not retrieval.
A control that prevents the search prevents R2 as well, and destroys the one path that
keeps researchers inside the governed channel.

**R10 — Enforcement must not depend on the model's cooperation.**
R1, R4, and R5 are all currently violated by the same mechanism: the model is persuaded.
Any control whose decision the model can argue with inherits the failure it was built to
fix.

**R11 — Enforcement must be evaluated on both axes simultaneously.**
Disclosure prevention and legitimate-research preservation must be reported as a pair.
Either number alone is uninterpretable: perfect refusal scores perfectly on one and
destroys the system.

**R12 — Enforcement must fail open.**
A broken policy evaluator must not take internal research offline. Availability failures
push work to the unenforced channel, which is a worse position than the ungoverned agent.
