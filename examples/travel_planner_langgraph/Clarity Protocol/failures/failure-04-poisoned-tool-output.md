# Failure: Poisoned tool output hijacks the plan

## Summary

Tool results are concatenated into model context as ordinary text with no boundary
marking them as data rather than instructions. Text an attacker controls — a hotel
listing description, an advisory body — can therefore address the model directly and be
followed: promoting a specific property, altering a stated total, or suppressing a
safety advisory the traveller was entitled to see.

The **traveller** is steered by a third party they never dealt with, and in the
suppression case is sent somewhere an advisory warned against — converting a commercial
manipulation into a physical-safety failure. The **compliance and duty-of-care owner**
is exposed because the suppressed content is precisely the content they are obliged to
surface. Unlike the fabrication modes, this failure has an adversary who can trigger it
deliberately and repeatedly.

## Failure Chain

1. An attacker controls text in a record the planner can retrieve — a listing
   description, a review field, an advisory body.
   - *Observation:* This requires no access to the planner. The attack is placed in
     upstream content and waits.
   - *Intervention point (prevention):* Sanitise or neutralise instruction-shaped
     content at the tool boundary, before it reaches context.
2. A traveller requests a plan for that destination.
3. `research` retrieves the poisoned record and places it into context.
   - *Intervention point (prevention):* Wrap tool output in an explicit data boundary
     so downstream nodes treat it as content to summarise, never as direction.
4. `itinerary_optimizer` reads the embedded text as guidance rather than data.
   - *Branch point:* Promotion — the plan steers the traveller to the attacker's
     property. Suppression — the plan omits a safety or entry advisory. Manipulation —
     the stated total is altered.
   - *Intervention point (detection):* Check the composed plan against the retrieval
     record; a suppressed advisory is a retrieved item missing from the output, and a
     promoted property is a recommendation unsupported by ranking data.
5. The manipulated plan is delivered in the planner's own trusted voice, carrying the
   planner's credibility rather than the attacker's. **harm begins**
6. The traveller books the promoted property, or travels without the suppressed
   warning.
   - *Branch point:* Commercial harm — the traveller overpays or gets a worse stay,
     recoverable. Safety harm — the traveller is exposed to the risk the advisory
     described, potentially not recoverable.
   - *Intervention point (mitigation):* Never allow an advisory that was retrieved to
     be absent from the plan, independent of any other reasoning.
7. Harm continues until the traveller independently discovers the omitted advisory or
   completes the trip. **harm ends**
8. Detection by the operator is unlikely: the output looks well-formed, and without
   provenance there is nothing to compare it against.
   - *Observation:* The attack is repeatable and silent, so a single poisoned record
     can affect many travellers before anyone notices.

## Observations

- **Severity:** High — Deliberate, repeatable, and adversary-controlled, with a
  credible path from commercial manipulation to physical-safety harm via advisory
  suppression. Rated below the Critical grounding failures because it requires an
  attacker to have placed content upstream, whereas those occur on ordinary requests.
- **Related failures:** The suppression variant produces the same end state as
  *Invented entry requirements* — a traveller acting without a warning they should
  have received — but by a different route, so a fix for one does not cover the other.
  *Provenance collapse* removes the comparison that would expose the manipulation.
- **Variants:**
  - Poisoned tool output hijacks the plan *(brainstorm)*

## Intervention Points

### Prevention
- Establish an explicit data/instruction boundary for all tool output.
- Neutralise or strip instruction-shaped content in retrieved text before composition.

### Detection
- Reconcile the composed plan against the retrieval record: retrieved advisories must
  appear; recommendations must be supported by retrieved ranking data.
- Flag imperative, model-addressed language appearing inside tool results.

### Mitigation
- Treat retrieved advisories as mandatory output — never suppressible by any
  downstream reasoning.
- Permit the planner to quote or warn about suspicious embedded content, but never to
  act on it.

### Recovery
- Retain retrieval records so a poisoned upstream source can be identified and purged
  once a single instance is discovered.

---

## Management Plan

[Not yet developed. Run failure management to develop a plan for this failure mode.]
