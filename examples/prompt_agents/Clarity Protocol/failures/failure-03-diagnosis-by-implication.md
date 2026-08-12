# Failure: Diagnosis by implication

## Summary

A patient describes symptoms and the assistant names what they have — usually not outright, but in
one of the hedged forms: *"that sounds like a UTI"*, *"this is consistent with low blood sugar"*,
*"could well be a side effect of the metformin"*.

The system prompt forbids diagnoses. The hedged forms are the ones that actually occur, because the
model is trying to help without violating the rule and hedging feels like compliance.

The hedge does not help. The patient does not hear a probability; they hear an answer, and the
answer resolves the uncertainty that was going to send them to a clinician. A softener changes the
assistant's exposure, not the patient's behaviour. The harm is displacement, which means a
*correct* hedged diagnosis is nearly as damaging as a wrong one — in both cases the visit that
would have produced a real diagnosis, with a real examination, does not happen.

## Failure Chain

1. A patient describes symptoms — fatigue, confusion, a new pain, something that started after a
   medication change.
2. The assistant recognises a pattern. Its priors are strong here and the patient is asking
   directly.
3. It hedges rather than declines, because hedging reads as compliance with "never provide
   diagnoses".
   *(Intervention point — the model's own safety check passes at exactly this step, which is why
   prompt-level mitigation does not reach it)*
   *(Branch — a `get_patient_profile` call returns simulator-invented conditions and medications,
   and the diagnosis is now built on a patient who does not exist)*
4. The assistant names the condition with a softener attached, frequently followed by a clinician
   referral.
5. The patient reads a conclusion.
   *(Intervention point — the outbound gate; attribution can be removed while the symptom concern
   and any urgency are preserved)*
6. The visit does not happen, or happens later. In an atypical presentation the delay is the whole
   mechanism.

## Observations

- **Severity:** High — It does not directly cause a physiological event the way a dosing error
  does; its damage runs through delayed care, which is slower and sometimes recoverable. It is High
  rather than Medium because in an elderly population delay is frequently the entire harm: sepsis,
  stroke, and cardiac events present atypically in older patients and are time-critical, and an
  assistant that resolves an atypical presentation into something benign-sounding buys the wrong
  kind of reassurance.
- **Related failures:** *Fabricated clinical fact presented as retrieved* supplies the false
  patient context this is often built on. *Clinician deferral omitted or reduced to boilerplate*
  determines whether a hedged diagnosis still routes the patient or terminates the process. *The
  enforcement layer itself fails* Branch A is the direct opposition: escalating a symptom requires
  engaging with it, so a gate that over-corrects here removes the assistant's most valuable
  function.
- **Variants:**
  - Hedged attribution to the patient *(brainstorm)* — "sounds like", "consistent with"; the
    dominant form
  - Diagnosis built on a fabricated patient profile *(brainstorm)* — a conclusion about someone who
    does not exist, delivered to someone who does
  - Symptom attributed to a medication side effect *(brainstorm)* — reads as medication information
    rather than diagnosis, so it evades topic-based gating
  - Benign explanation offered for an atypical presentation *(brainstorm)* — the highest-harm form,
    because the reassurance is what causes the delay
  - Differential framed as a list of possibilities *(brainstorm)* — "could be A, B, or C" feels
    safer and still resolves the question enough to stop the visit
  - Patient's own suggested diagnosis confirmed *(brainstorm)* — the patient proposes it and the
    assistant agrees; agreement carries the same weight as assertion

## Intervention Points

### Prevention
- Nothing at the prompt level reaches this. The prohibition is already stated and the hedged form
  is the model's attempt to honour it.
- Do not prevent by topic. Symptom discussion is legitimate and necessary — the assistant is
  supposed to tell people when something warrants attention, and that requires engaging with the
  symptom.

### Detection
- The boundary is **attribution**, not topic. "Sudden one-sided weakness can indicate a stroke and
  needs emergency assessment now" is escalation; "this sounds like a stroke" is a diagnosis. The
  clinical content overlaps almost entirely; the grammatical stance does not.
- This is irreducibly semantic. No pattern matches the difference between describing what a symptom
  can indicate and assigning a condition to the person asking.

### Mitigation
- The condition is not named. Marking fails here for the same reason as the dosage mode and more
  so: a hedged diagnosis is *already* marked. Appending "but see a doctor" to "this sounds like a
  UTI" produces exactly the output being prevented.
- The concern is stated. Symptoms get substantive engagement, and urgent presentations get escalated
  clearly and quickly. What is removed is the sentence telling the patient what they have.
- Escalation must survive the gate intact. Suppressing urgency to avoid attribution is a worse
  outcome than the failure being prevented.

### Recovery
- Weak. A later turn can withdraw an attribution, but the patient's decision not to seek care is
  made on the first reading and is not revisited.
- The reassurance variant has effectively no recovery path, because a reassured patient does not
  come back to ask again.

## Management Plan

Enforce at the outbound text on attribution: is a condition being assigned to this patient,
including hedged and confirmatory forms. When it fires, regenerate with the attribution removed and
the symptom concern plus any warranted urgency preserved, then re-gate.

Read success as symptom questions continuing to receive substantive engagement and urgent
presentations continuing to be escalated, while condition-naming disappears. If symptom
descriptions start returning generic deflections, the control has disabled the assistant's highest-
value behaviour — recognising when someone needs to be seen — and the harm reduction is not real.
