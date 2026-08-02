# Requirements

## R1 — The prohibition must be enforced per reply, not per prompt

The system prompt already forbids dosing and diagnosis. It is not enough, because
it is a disposition and dispositions erode (F3).

Every outbound reply is inspected before it reaches the user. This is the whole
point: the control has to be as strong at turn nine as at turn one, and the only
way to guarantee that is to check the reply itself rather than trust the standing
instruction.

## R2 — Deterministic pre-verdicts for the two known shapes

Two checks run before any model call and cannot fail open:

- **`_DOSE_DIRECTIVE`** — a concrete quantity paired with an administration verb
  in the same clause. Catches F1.
- **`_REASSURANCE`** — a dismissal of clinical concern about a symptom the user
  described. Catches F2.

They are deterministic because these two shapes are the measured failure and must
not depend on a second model's mood. A semantic judge that is right 95% of the
time is not good enough for a dose figure delivered to an elderly patient.

## R3 — Refusal of the dose must not be refusal of the person

When a request is blocked, the reply must still deliver everything legitimate in
the surrounding conversation: what the medication does, what the side effect
profile looks like, what the term means, what to ask the prescriber, and — where
warranted — an explicit escalation to urgent care.

Baseline overrefusal is 8.0% on scenarios. This requirement exists because that
number is low and worth protecting, and because a stonewalling health assistant
is not a safer health assistant — it is one the user stops consulting.

## R4 — Clinical framing supplied by the user is never a warrant

"Her doctor already prescribed it", "the pharmacist told me to ask" — these are
unverifiable claims about absent third parties. They do not unlock dosing.

Addresses F4, the most reliable lever in the failing scenarios. The rule has to be
stated explicitly because the framing is *designed* to feel like authorization.

## R5 — Escalation is always permitted

Telling a user to seek immediate care can never be blocked, whatever it
structurally resembles. Some urgent-care advice looks like a directive; it is the
one directive that is always correct.

Without this carve-out the gate would suppress exactly the output that matters
most in the highest-risk conversations.

## R6 — A conversation-scoped clinical ledger

What the user has disclosed — medications, symptoms, the patient's age and
relationship — accumulates across turns and is available to every check.

Required because F3 is a multi-turn failure: pressure is built incrementally, and
a turn-local view cannot see the arc that produced the violation.

## R7 — The semantic annotator is a backstop and fails open

The LLM annotator runs after the deterministic checks and can only add findings.

It returns the **literal string `"true"` / `"false"`**, matching the generated
Rego, which compares against `"true"` rather than a boolean. Returning a real
Python `True` silently never matches and fails open with no error — a genuine
trap, and the fifth mutually incompatible annotator contract in this batch.

## R8 — Regeneration preserves the useful part

Up to two rewrite attempts. The rewrite keeps all permissible content and
replaces only the prohibited instruction with a clear statement of why it cannot
be given and who can give it.

The fallback leads with what the agent *can* do, not with an apology. Leading
with refusal is what the overrefusal dimension scores.
