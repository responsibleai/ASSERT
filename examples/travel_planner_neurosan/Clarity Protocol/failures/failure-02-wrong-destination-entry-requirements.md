# Failure: Entry requirements for the wrong destination

## Summary

`check_travel_advisories` returns one fixed payload regardless of the `region` argument:

> `visa_required: True`, "Tourist visa or visa waiver (90 days)", "Level 1 - Exercise Normal
> Precautions", "Japanese encephalitis risk in rural areas", "Earthquake preparedness
> recommended"

It echoes back whatever region label it was given, so a request for France produces Japan's
entry requirements titled "France". The system prompt instructs the agent to "surface visa
requirements, safety advisories, and health precautions", and it complies — faithfully relaying
a tool result that answers a question the tool was never able to answer.

There is nothing anomalous at the call boundary. `check_travel_advisories` is invoked with the
correct region, returns successfully, and its span records a well-formed result. The agent is
not fabricating; it is accurately reporting false data. That distinction matters, because it
means no check on the agent's fidelity to its tools can detect this — fidelity is exactly what
produces the harm.

The consequence separates this from every other failure here. A wrong price is discovered when
money runs out and can be absorbed. A wrong visa statement is discovered at an airline counter
or a border control desk, where the traveller is denied boarding, refused entry, or detained,
having already paid for the trip. There is no correction available at that point.

## Failure Chain

1. A traveller asks about a trip to a destination outside Japan.
   - *Observation:* Any destination other than Japan produces the failure. Japan is a single
     point in the space of possible requests, so the correct case is the exception.
2. `classify_intent` extracts a `region`. It may be correct, or it may fall back to "Japan" if
   intent parsing failed.
   - *Observation:* This creates two distinct routes to the same harm — a correct region against
     a fixed payload, or a wrong region entirely — which any gate keyed on region comparison must
     be able to tell apart.
3. `check_safety` calls `check_travel_advisories` with that region.
   - *Intervention point (prevention):* Not reachable here. The call is correct; the argument is
     correct; the tool succeeds. There is no structural signal to gate on.
4. The tool returns Japan's advisory payload with the requested region label attached.
   - *Observation:* The label is the trap. `{"region": "France", visa_type: "Tourist visa or visa
     waiver (90 days)", ...}` reads as a France-specific answer to every downstream consumer,
     including the summarizing LLM and the optimizer.
5. The safety summarizer compresses it to prose, and the optimizer incorporates it into the
   itinerary as instructed.
   - *Intervention point (detection):* Evaluate whether the entry-requirement claims in the
     itinerary are attributable to the destination being planned. This is a semantic judgement
     about the output, with no tool-call equivalent.
6. The traveller reads authoritative-sounding entry requirements for their destination. **harm
   begins**
   - *Observation:* This is precisely the information a traveller cannot verify themselves and
     asked the agent for. The fluency and specificity of the payload — a named visa type, a
     numbered safety level, a specific disease — make it more credible than a vaguer correct
     answer would be.
   - *Intervention point (mitigation):* Mark unattributable entry requirements as unverified at
     the point they appear and direct the traveller to an authoritative source. Do **not**
     suppress them: silence reads as "nothing required", which is the same harm reintroduced.
7. **Branch point — visa-waiver passport, permissive destination.** The advice happens to be
   roughly right. No harm occurs, and the traveller's trust in the agent's visa guidance is
   reinforced for the next trip.
8. **Branch point — visa required.** The traveller arrives without one and is denied boarding or
   refused entry. **harm begins in earnest** — money lost, trip lost, and in some
   nationality/destination pairs, detention.
   - *Observation:* The harm is inversely distributed to need. A traveller who requires no visa
     is told something roughly right by accident; the traveller who genuinely needs one — with
     the most at stake and the strongest reason to have asked — receives the most confidently
     wrong answer.
9. **Branch point — health.** The traveller prepares for Japanese encephalitis and earthquakes
   while the actual risks at their destination are never mentioned, because no tool ever returned
   them. Omission here is invisible in a way that a wrong statement is not.
10. **harm ends** only after the traveller is turned back, returns home, or completes the trip
    having been lucky.
    - *Intervention point (recovery):* Retain the advisory payload alongside the destination so
      itineraries carrying mismatched entry requirements can be identified and travellers warned
      before departure.

## Observations

- **Severity:** Critical — Harm to a traveller's liberty and finances, discovered at a border
  where no correction is possible, reached on the normal path for every destination but one. The
  inverse distribution is the aggravating factor: the control fails hardest for the traveller
  with the most to lose. Rated alongside the budget failure rather than above it because it
  requires the destination to be non-Japan and a visa to actually be required, whereas the budget
  fabrication fires unconditionally.
- **Related failures:** Unlike *Fabricated budget verification*, this has no structural signature
  — the tool is called correctly and returns successfully — so it requires a semantic check on
  the output rather than a comparison against the log. *Silent default trip parameters* supplies
  a second route to it by defaulting `region` to "Japan". The suppression branch of *The
  enforcement layer itself fails* is the specific way a fix for this mode recreates its own harm.
- **Variants:**
  - Advisories describe the wrong country *(brainstorm)* — fixed payload, echoed region label
  - Visa waiver asserted for any passport *(brainstorm)* — inverse harm distribution
  - Wrong health and safety precautions given *(brainstorm)* — plus silent omission of real risks

## Intervention Points

### Prevention
- No tool-call gate reaches this. The call is correct and succeeds; only the output can be
  checked.

### Detection
- Evaluate semantically whether entry-requirement claims in the itinerary are attributable to the
  destination being planned, grounded against the advisory payload actually returned.
- Distinguish a mismatched payload from a misextracted `region`, since both produce the same
  symptom by different routes.

### Mitigation
- Mark unattributable entry requirements as unverified where they appear and direct the traveller
  to an authoritative source.
- Never suppress advisories outright — silence is read as "nothing required".

### Recovery
- Retain the advisory payload with the destination so affected itineraries can be identified and
  travellers warned before departure.

---

## Management Plan

[Not yet developed. Run failure management to develop a plan for this failure mode.]
