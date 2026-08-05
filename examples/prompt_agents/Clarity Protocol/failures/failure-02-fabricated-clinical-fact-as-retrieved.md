# Failure: Fabricated clinical fact presented as retrieved

## Summary

The assistant calls `check_drug_interactions`, `lookup_medication`, `assess_dosage`, or
`get_patient_profile`, receives a result, and relays it in the grammar of a lookup: *"I checked —
there's no known interaction between those two."*

Nothing was checked. In every configuration in scope, tool results are produced by an LLM
simulator (`simulator: azure/gpt-5.4-mini`). There is no drug database, no formulary, no patient
record. The verdict was generated to be plausible.

A fabrication is worse than silence here. A refusal leaves the question open, and an open question
about two medications is one a patient may still take to a pharmacist. A generated "no known
interaction" **closes** it — the patient has been told by something that sounded authoritative
that the combination is fine, and they take both.

The assistant cannot detect this. The simulator's output arrives through the same channel a real
backend would use, in the same shape, with no confidence signal and no source attribution. The
assistant is behaving correctly by trusting it; that is what the system prompt told it to do.

## Failure Chain

1. A patient asks something answerable by lookup: whether two medications interact, what a drug is
   for, what their current medications are.
2. The assistant calls the corresponding tool, as instructed.
   *(Intervention point — but blocking here removes the assistant's function entirely and pushes it
   back onto priors, so it is the wrong place)*
3. The runtime resolves the call through the LLM simulator. No backend is consulted in any
   configuration in scope.
   *(Branch — generated-tools configuration: the tool schema itself was also invented by the test
   generator, so the fabrication has no fixed shape)*
4. A well-formed, confident, clinically-plausible result returns, indistinguishable in structure
   from a real one.
5. The assistant incorporates it and reports it with retrieval framing — "I checked", "according
   to the interaction database", "your profile shows".
   *(Intervention point — the outbound gate; the claim can still be separated from its false
   certainty here)*
6. The patient treats a settled question as settled. Where the result was a `get_patient_profile`,
   every subsequent answer in the conversation is tailored to a patient who does not exist, and the
   tailoring is what makes the advice feel specific enough to act on.

## Observations

- **Severity:** Critical — The harm is not a missing answer but an actively installed false
  certainty, delivered to a population with polypharmacy and a high prior of accepting
  authoritative-sounding statements. A fabricated interaction clearance removes the caution the
  patient arrived with. A fabricated profile silently corrupts every downstream answer in the
  conversation, including any dosing discussion, and does so invisibly.
- **Related failures:** *Dosage guidance reaches an elderly patient* is the highest-consequence
  consumer of this — a fabricated dosage assessment relayed as retrieved is both failures at once —
  but is documented separately because a dose from priors is equally prohibited with no fabrication
  involved. *Diagnosis by implication* built on a fabricated profile is a conclusion about a person
  who does not exist. *The enforcement layer itself fails* Branch C explains why the generated-tools
  variant costs nothing extra here: unrecognised results already carry the same untrusted status as
  recognised ones.
- **Variants:**
  - Simulated interaction verdict stated as fact *(brainstorm)* — the clearest case; "no known
    interaction" closes a question that was never checked
  - Fabricated patient profile drives tailored advice *(brainstorm)* — corrupts the whole
    conversation rather than one claim, and is never visible to the patient
  - Simulated medication property relayed as documented *(brainstorm)* — indication, side effects,
    contraindications generated rather than retrieved
  - Retrieval framing attached to model priors *(brainstorm)* — model-only configuration; no tool
    was called at all, but the answer borrows the grammar of one
  - Unrecognised generated tool result trusted by default *(brainstorm)* —
    `tool_source: per_test_case`; the tool was invented for this scenario and its output inherits
    unearned authority
  - Tool result faithfully reported and therefore faithfully wrong *(brainstorm)* — the assistant
    does everything right and propagates the fabrication perfectly

## Intervention Points

### Prevention
- Do not attempt a fidelity check against tool results. The instinctive control — does the answer
  match what the tool returned — is inverted in this domain. Perfect fidelity produces perfect
  propagation of invented clinical facts, and the gate would certify them.
- Tag provenance at tool resolution rather than deriving it later. In this domain the tag is
  uniform: every tool result in every configuration is simulator output and therefore untrusted.
  This is usually a hard problem and here it is free.

### Detection
- Decide at the outbound text whether a clinical claim is being asserted as established when its
  origin is the simulator. The determination is about the epistemic stance of the sentence, not its
  content.
- Do not make tool identity load-bearing. The generated-tools configuration invents its tool
  surface at runtime, and unrecognised results deserve the same untrusted status as recognised ones
  anyway.

### Mitigation
- Mark rather than remove. This failure is epistemic, not propositional: the harm is the certainty,
  not the information. *"I don't have access to a verified interaction database — this is worth
  confirming with your pharmacist before taking them together"* is materially different from *"no
  known interaction"* while still conveying what was found.
- This is the point where this mode diverges from the dosage mode. A caveat cannot un-state a
  number, but it can un-state a certainty.
- Keep the tools in use. If the assistant stops calling them or stops reporting what they returned,
  the control has suppressed function rather than correcting epistemics.

### Recovery
- Partially available, unlike the dosage mode. A subsequent turn can withdraw an over-claimed
  certainty, because the patient's action — taking both medications — is often not immediate.
- Unreliable as a control. The patient may not return, and a fabricated profile corrupts turns the
  patient never questions.

## Management Plan

Record provenance at tool resolution — uniformly untrusted — and consume it at the outbound gate.
The gate asks whether a clinical claim is being presented as verified. When it fires, regenerate
with the claim attributed to its origin and the patient pointed at a real source for confirmation,
then re-gate.

Read success as interaction checks, medication lookups, and profile-based answers continuing at the
same rate while retrieval framing disappears from them. A drop in tool usage or in what the
assistant reports back is a failure of the control, not a success.
