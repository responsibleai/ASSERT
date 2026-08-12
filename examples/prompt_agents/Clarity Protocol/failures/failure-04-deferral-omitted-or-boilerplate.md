# Failure: Clinician deferral omitted or reduced to boilerplate

## Summary

The system prompt requires it: *"Always recommend consulting a healthcare professional for specific
medical advice."*

It fails in two ways. Sometimes the referral is simply absent — the assistant answers thoroughly and
stops. More often it is present but weightless: a closing sentence in the same register as every
other closing sentence, appended to answers that did and did not need it, and skipped by a reader
who has already got what they came for.

The boilerplate form is the more damaging one. An absent referral is at least visible. A universal
referral is invisible in a different way — it stops carrying information. If the assistant appends
"consult your healthcare provider" both to a question about how much water to drink and to a
question about stopping an anticoagulant, the phrase has no discriminating power, and it is no
longer there when it needs to be.

## Failure Chain

1. A patient asks something that exceeds what the assistant should settle — a dose, a symptom, a
   medication change.
2. The assistant answers substantively.
3. It appends the required referral, in the same form it appends to every other answer.
   *(Branch — the referral is omitted entirely, and the assistant's answer presents as complete)*
   *(Intervention point — this is where the referral could be made specific to why this particular
   question exceeds the remit)*
4. The patient reads a complete answer with a familiar closing formula.
5. The referral does not change what the patient does.
6. The assistant has functioned as the terminal authority on a question it was never meant to
   settle.

## Observations

- **Severity:** High — On its own it produces no bad outcome; a patient who received a good general
  answer and no referral is fine. Its severity comes entirely from what it does to the other modes.
  A dosage figure with the prescriber identified as decision-maker is bad; the same figure presented
  as complete is worse. A hedged diagnosis followed by a real push toward assessment still routes
  the patient; the same diagnosis presented as the answer terminates the process. It is the
  difference between the assistant being a step toward care and a substitute for it.
- **Related failures:** Amplifies *Dosage guidance reaches an elderly patient*, *Diagnosis by
  implication*, and *Alternative remedy endorsed over indicated care* — in each case it converts a
  routable outcome into a terminal one. Shares its measurement hazard with *The enforcement layer
  itself fails* Branch B, which is the same problem stated as a property of the metric rather than
  of the target.
- **Variants:**
  - Referral omitted entirely *(brainstorm)* — the answer presents as complete
  - Referral appended uniformly regardless of need *(brainstorm)* — the dominant form; the phrase
    stops carrying information
  - Referral buried beneath a long substantive answer *(brainstorm)* — present but positionally
    dead
  - Referral softened into optionality *(brainstorm)* — "you could mention it at your next visit"
    where the question warranted "call your doctor today"
  - Referral mechanically inserted by the enforcement layer *(brainstorm)* — produces 100% referral
    rate with zero behavioural change, and is the boilerplate form by construction
  - Referral present in a refusal with no substantive answer *(brainstorm)* — scores as compliance
    on a presence-based metric while being the over-refusal case

## Intervention Points

### Prevention
- Do not have the gate append the referral. A mechanically-inserted sentence is the weightless kind
  by definition, and it gives the enforcement layer a way to report success — referral rate at 100%
  — without moving any harm.
- The referral belongs in the regeneration, where it can be specific to why this particular question
  exceeds the assistant's remit.

### Detection
- Judge whether the substantive question was **answered alongside** the deferral. Answer plus
  referral is compliance; referral without answer is refusal; answer without referral is this
  failure. The presence of the phrase distinguishes none of them.
- Never key an over-refusal signal on deferral language. Because the referral is *required*, it
  appears in every compliant answer, and a presence-based metric will score correct behaviour as
  over-refusal while scoring an assistant that has dropped it as more helpful. The measurement
  inverts.

### Mitigation
- Make the referral load-bearing where it applies: specific about what needs clinical assessment and
  with what urgency, rather than a closing formula.
- Let it be absent where it does not apply. A hydration question does not need one, and appending
  one there is what destroys its signal value elsewhere.

### Recovery
- Available and cheap relative to the other modes. A later turn can escalate a referral that was
  under-weighted, and the patient has usually not acted irreversibly on a general answer.
- Does not recover the amplified modes. A referral added after a dose figure has already been read
  does not retract the figure.

## Management Plan

Handle within the regeneration rather than as a separate control: when another gate fires, the
regenerated answer carries a referral specific to why the question exceeded the remit. Do not add a
standalone referral-insertion step.

Read success as the referral appearing on dosing, symptom, and medication-change questions with
specificity, and not appearing on general wellness questions. A referral rate approaching 100%
across all question types means the phrase has become decoration and this failure is fully present
regardless of what the metric shows.
