# Failure: Source and class attribution collapse

## Summary

The agent states facts without naming the document they came from or its sharing class. The
system prompt requires the opposite — "always attribute factual claims to a retrieved document
and its class" — and that requirement is doing more work than it appears to.

Attribution is not a courtesy or a formatting preference in this system. It is the **only**
mechanism by which any other failure here becomes visible. There is no access log; a disclosing
answer and a legitimate answer are byte-for-byte indistinguishable to compliance. The class
label attached to a claim is the sole artifact that lets a reader, a reviewer, or an auditor
determine whether a statement should have been made.

Remove it and every other failure in this portfolio becomes silent:

- A restricted figure reads as a research finding.
- A laundered paraphrase reads as a public result.
- A fabricated corroboration reads as a real citation.
- A successful injection reads as a helpful answer.

This is the same structural role that provenance loss plays in documentation systems: not a
root cause, but the amplifier that converts every one-off failure into an invisible, recurring
pattern.

## Failure Chain

1. The agent retrieves a mix of classes on a normal query — token-overlap ranking routinely
   returns public, internal-only, and restricted documents together for a single topical query.
   - *Observation:* Mixed-class result sets are the norm rather than the exception, which is
     precisely why per-claim attribution matters more here than in a single-tier system.
2. The agent synthesises an answer across several documents.
   - *Observation:* Synthesis is the agent's core value. The failure is not that it synthesises
     but that the synthesis discards the tier metadata that arrived with each input.
   - *Intervention point (prevention):* Make per-claim attribution structural — carry `id` and
     `class` through into the reply rather than leaving it to the model's formatting choices.
3. Claims are stated without their document id and class.
   - *Intervention point (detection):* Check that factual claims carry an attribution before the
     reply is released; an unattributed claim is itself a reportable condition.
4. The researcher reads a set of undifferentiated facts. **harm begins** — not because any single
   claim is wrong, but because the reader has lost the ability to evaluate any of them.
   - *Observation:* This is the hinge for the whole portfolio. Attribution is the last point at
     which a human could notice a tier violation in the moment. Past it, every other chain runs
     to completion unobserved.
   - *Intervention point (mitigation):* Present retrieved sources and their classes as a
     distinct part of the reply, so the reader sees the tier mix even if a claim is unattributed.
5. **Branch point — onward use.** The researcher forwards or drafts from the material, unable to
   tell which parts were shareable. A restricted fact travels with the same apparent standing as
   a public one.
6. **Branch point — audit.** Compliance reviews agent behaviour and finds nothing anomalous,
   because nothing anomalous is recorded. They certify a control that is not working.
   - *Observation:* False assurance is worse than known ignorance: it forecloses the
     investigation that would have found the disclosures.
7. Individual harms end as their exposures stop expanding. **harm ends** per incident.
8. The pattern recurs indefinitely, because nothing surfaces it. The disclosure rate is
   unmeasurable, so it cannot be managed.
   - *Intervention point (recovery):* Log retrieved ids and classes per turn independently of the
     reply, so historical analysis can reconstruct which conversations carried restricted
     material even when the reply omitted attribution.

## Observations

- **Severity:** High — No direct harm in isolation; it is the failure that removes both the
  researcher's in-the-moment check and compliance's after-the-fact check. It sets the recurrence
  rate of every other mode in this portfolio, and it defeats the specific evidence compliance
  relies on to know whether the tier system is holding. Its intervention value is far larger
  than its own harm.
- **Related failures:** Amplifies *Restricted-class content disclosed in a reply*,
  *Restricted content laundered through paraphrase or inference*, *Fabricated public
  corroboration*, and *Instruction embedded in a retrieved document is obeyed* — each of which
  becomes detectable if attribution is present and silent if it is not. Distinct from those in
  that it is a property of every reply rather than of a specific harmful one.
- **Variants:**
  - Source and class attribution omitted *(brainstorm)*

## Intervention Points

### Prevention
- Carry `id` and `class` through into the reply structurally, rather than depending on the
  model's formatting discipline.
- Where content was withheld, say so and name the class — a redaction marker that states the
  class is itself an attribution.

### Detection
- Check that factual claims carry an attribution before release; treat unattributed claims as a
  reportable condition rather than a style issue.

### Mitigation
- Present retrieved sources and their classes as a distinct section of the reply so the tier mix
  is visible even where individual claims are unattributed.

### Recovery
- Log retrieved ids and classes per turn independently of the reply, enabling reconstruction of
  which conversations carried restricted material.

---

## Management Plan

[Not yet developed. Run failure management to develop a plan for this failure mode.]
