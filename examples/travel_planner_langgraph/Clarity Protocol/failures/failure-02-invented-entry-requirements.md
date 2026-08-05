# Failure: Invented entry and health requirements

## Summary

The planner asserts visa, entry, and health requirements — "no visa needed for stays
under 90 days", "no vaccinations required" — that `check_travel_advisories` never
returned. The mechanism is the same composition-without-retrieval gap that produces
fabricated costs, but the harm class is different and worse: the traveller cannot pay
their way out of it. They arrive without a required document and are refused boarding
or refused entry.

The **traveller** loses the entire trip — flights, lodging, and committed leave — and
may face a re-entry ban. The **compliance and duty-of-care owner** carries the
regulatory exposure, because advice about border and health requirements is
consequential guidance regardless of the disclaimers around it. This is compounded by
the fact that entry rules change frequently, so a model answering from parametric
recall is stale by construction even when it is not inventing.

## Failure Chain

1. Traveller asks whether they need a visa, a vaccination, or any entry document — or
   simply requests a plan for a destination where such a requirement exists.
   - *Observation:* The question is often implicit. A traveller who does not know a
     visa is required will not think to ask, so the planner's silence is itself an
     answer.
2. `check_travel_advisories` is skipped, errors, or returns no entry data.
   - *Intervention point (prevention):* Treat entry/health topics as requiring a
     successful advisory lookup before any answer may be composed.
3. `itinerary_optimizer` answers from parametric knowledge, or omits the requirement
   entirely from an otherwise complete plan.
   - *Branch point:* Explicit false assertion ("no visa required") vs. silent omission.
     Omission is harder to detect and equally harmful, since the plan reads as complete.
   - *Intervention point (detection):* Require that any entry, visa, or health claim be
     traceable to advisory-tool output, and treat unsupported omission of a returned
     advisory as a violation too.
4. The claim is rendered in the same confident register as tool-sourced content, and
   is often the kind of statement a traveller has no independent reason to doubt.
   - *Intervention point (mitigation):* Attribute advisory claims to their source and
     direct the traveller to the authoritative government source for confirmation.
5. Traveller relies on it and does not obtain the document or vaccination.
   - *Observation:* Reliance here is reasonable behaviour, not carelessness. The
     planner presented itself as having checked.
6. Traveller books and pays for a trip they are not eligible to take.
7. Traveller is denied boarding at departure, or refused entry on arrival.
   **harm begins**
   - *Branch point:* Denied at departure — trip lost, traveller is home. Refused on
     arrival — traveller is stranded abroad, additional cost and risk, materially worse.
8. Traveller absorbs non-refundable losses, forfeits leave, and in the arrival case
   arranges emergency return travel. **harm ends** once they are home or the trip is
   formally abandoned.
   - *Intervention point (recovery):* None meaningful at this stage. The harm is
     realised and largely unrecoverable, which is why prevention and detection carry
     the entire weight for this failure mode.
9. The traveller may pursue the operator over consequential advice. Compliance learns
   of the failure through a complaint or a claim rather than through monitoring.
   - *Observation:* Awareness arrives late and externally, so the same wrong advice may
     have been given many times before anyone notices.

## Observations

- **Severity:** Critical — Non-recoverable harm. Unlike a wrong price, no amount of
  additional spend fixes a missing visa at the gate. Carries regulatory and
  duty-of-care exposure for the operator, and the "silent omission" variant is
  invisible to a traveller doing ordinary sanity-checking.
- **Related failures:** Shares the root mechanism and the "no retrieval occurred"
  trigger with *Fabricated trip costs*, but requires different enforcement: cost claims
  are checked against retrieved figures, whereas advisory claims must be checked
  against retrieved advisory text including its absence. Interacts with *Poisoned tool
  output*, where an attacker can cause an advisory to be suppressed deliberately.
- **Variants:**
  - Invented visa or entry requirement sends a traveller to a border they cannot cross
    *(brainstorm)*
  - Silent omission of a returned advisory from the composed plan *(identified during
    analysis — the omission form of the same claim failure)*

## Intervention Points

### Prevention
- Require a successful advisory lookup before any entry, visa, or health topic may be
  answered; refuse to compose rather than compose from recall.
- Treat this claim class as never answerable from model knowledge, given that entry
  rules change faster than model training.

### Detection
- Verify every entry/visa/health assertion against advisory-tool output for this
  conversation.
- Detect the omission case: an advisory that was retrieved but does not appear in the
  composed plan.

### Mitigation
- Attribute advisory content to its source rather than paraphrasing it into a stronger
  or weaker guarantee.
- Always direct the traveller to the authoritative government source for confirmation,
  so the planner is never the sole basis for an entry decision.

### Recovery
- Effectively none once the traveller is at the border. Weight must sit on prevention
  and detection.

---

## Management Plan

[Not yet developed. Run failure management to develop a plan for this failure mode.]
