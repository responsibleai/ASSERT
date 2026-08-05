# Failure: The grounding check itself fails

## Summary

The proposed enforcement layer introduces its own failure modes, and they sit on a
single tuning boundary: how the policy decides whether a claim is load-bearing and
unsupported. Match too broadly and legitimate qualitative answers are suppressed, the
planner becomes useless for the open questions travellers actually ask, and operators
switch the check off — restoring the original fabrication risk in full. Match too
narrowly and invented specifics pass unrecognised, while the presence of a check
manufactures unearned confidence in both the traveller and the operator. A third
variant sits on the retry path: repeated denial that loops or degrades to an empty
answer, delivering the worst experience to exactly the users whose questions were
hardest to ground.

The **traveller** is harmed either by evasive non-answers or by fabrications that now
carry an implicit seal of approval. The **operator** is harmed because both extremes
lead to the check being abandoned. These are grouped because they share one mechanism
and one remedy — calibrating the claim boundary — and because they must be measured
together: a fix that reduces fabrication while suppressing legitimate behaviour is not
a fix.

## Failure Chain

1. Enforcement is enabled. Every composed response is evaluated before release.
2. The policy classifies claims in the draft as load-bearing and supported, or not.
   - *Observation:* This classification is the least certain part of the design and
     the origin of all three variants.
3. **Branch A — over-broad matching.** A hedged or qualitative statement ("March is
   usually mild", "flights tend to run around €200") is classified as an unsupported
   specific.
   - *Intervention point (prevention):* Define load-bearing claims as asserted
     specifics tied to this trip, explicitly excluding acknowledged estimates and
     general observations.
   4. The response is denied and regenerated with the content stripped or over-hedged.
   5. The traveller receives an evasive non-answer to a reasonable question.
      **harm begins**
   6. Usefulness degrades across ordinary exploratory use; operators disable the check.
   7. **harm ends** for overrefusal, and every original fabrication mode returns
      unmitigated — a strictly worse end state than never having added the check.
      - *Intervention point (detection):* Measure suppression of acceptable behaviour
        alongside harm reduction, so this branch is visible before rollout rather than
        after.
4. **Branch B — missed fabrication.** An invented specific appears in a form the
   matcher does not recognise — unusual formatting, a figure stated in prose, an
   advisory paraphrased into a sentence.
   5. The policy finds no violation and allows the response unchanged.
   6. Because a grounding check is known to be in place, both traveller and operator
      trust the output more than they did before it existed. **harm begins**
   7. The traveller verifies less than they otherwise would, and the underlying
      fabrication harm lands with reduced resistance.
      - *Observation:* This is worse than no enforcement, because the check's existence
        removes the scepticism that previously provided partial protection.
      - *Intervention point (detection):* Validate coverage empirically — confirm the
        measured harm rate actually falls rather than assuming a policy implies
        coverage.
5. **Branch C — regeneration failure.** A denied draft is regenerated and denied again.
   6. Each cycle costs another model call on an already-failing turn.
      - *Intervention point (prevention):* Bound the retry count explicitly.
   7. The turn either hangs past any acceptable latency or degrades to a near-empty
      answer. **harm begins**
   8. The traveller abandons the planner for precisely the requests it handles worst.
      **harm ends**
      - *Intervention point (mitigation):* Degrade to a useful form — lead with
        supported content and mark the rest unconfirmed — never to a flat refusal.

## Observations

- **Severity:** High — Each branch either negates the solution's benefit or produces a
  net-worse outcome than the unguarded baseline. Branch B is the most insidious because
  it converts a visible risk into an invisible one.
- **Related failures:** Directly determines whether *Fabricated trip costs* and
  *Invented entry requirements* are actually mitigated. Branch B compounds with
  *Provenance collapse*: without provenance, a false negative in enforcement is as
  undiagnosable as the fabrication it missed.
- **Variants:**
  - Grounding check suppresses legitimate answers *(brainstorm)* — Branch A
  - Policy misses a fabrication and confers false trust *(brainstorm)* — Branch B
  - Regeneration loops or degrades to an empty answer *(brainstorm)* — Branch C

## Intervention Points

### Prevention
- Scope load-bearing claims narrowly and explicitly: asserted specifics about this
  trip, not general observations or acknowledged estimates.
- Bound regeneration attempts; define the degraded form in advance.
- Fail open on evaluator error — an enforcement layer must not take the planner offline
  when it malfunctions.

### Detection
- Measure harm reduction and suppression of acceptable behaviour as a paired result;
  neither number is interpretable alone.
- Treat an unchanged harm rate under an active policy as evidence of Branch B rather
  than evidence of a clean baseline.

### Mitigation
- Degrade to supported-content-first answers with explicit unconfirmed markers.
- Never emit a flat refusal as the enforcement outcome.

### Recovery
- Keep the claim definition and policy as declarative, reviewable artifacts so the
  boundary can be retuned without rewriting the agent.

---

## Management Plan

[Not yet developed. Run failure management to develop a plan for this failure mode.]
