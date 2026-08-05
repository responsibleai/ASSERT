# Failure: The enforcement layer itself fails

## Summary

The control that fixes the other failures has three of its own, and they share one mechanism:
the boundary the gate draws must match the boundary policy actually draws, and the gate's
output must leave the agent able to continue working.

**Over-redaction.** The gate withholds material that policy permits. `internal_only` is the
exposed tier — explicitly discussable under rule 3, adjacent to the restricted set, and the
first casualty of any control scoped by topic or proximity rather than by the `class` field.
Public dataset cards and the external-safe publication plan are next.

**Retry loops.** `MAX_TOOL_CALLS` and `MAX_STEPS` are both 6, low for this repo. A denial or an
uninformative redaction marker invites the model to search again with different phrasing. Three
retries exhaust the budget and the turn ends with no answer on a legitimately answerable
question.

**Under-enforcement.** The gate redacts `body` but not `snippet`, so `file_partner_001`'s recall
figure and `file_contact_001`'s owner and alias still reach context. Disclosure continues while
the system reports enforcement is active — and now attracts *less* scrutiny than before the gate
existed.

All three converge on the same end state, and it is specific to this domain: researchers stop
using the agent and go to the share drive, **where no sharing tier is enforced at all.** The
ungoverned agent is not the fallback. An unenforced channel is. That makes availability and
usability security properties here, not merely quality properties.

## Failure Chain

1. Enforcement is enabled. Tool results pass through policy evaluation before entering the
   transcript.
2. **Branch A — over-redaction.** The gate's boundary is drawn by topic, keyword, or proximity
   to sensitive material rather than by the `class` field.
   - *Intervention point (prevention):* Scope the boundary to exactly `RESTRICTED_CLASSES`. The
     authoritative label is returned with every result; nothing needs to be inferred, and
     inferring widens the boundary in both directions for no gain.
   3. A researcher asks about the long-context evaluation harness — answerable from the
      internal-only summary, which rule 3 permits discussing. The gate withholds it. **harm
      begins**
      - *Observation:* `internal_only` is the sentinel tier. Its survival is the single best
        indicator of whether enforcement is correctly scoped, because it is permitted, adjacent,
        and topically entangled with the restricted documents.
   4. The researcher concludes the agent cannot help with internal questions and stops asking.
      **harm ends** for them; the coverage loss is permanent.
3. **Branch B — retry loop.** The gate returns a denial, or a bare `[REDACTED]` with no
   explanation.
   4. The model cannot distinguish "withheld by policy" from "search failed" and reformulates.
      - *Intervention point (prevention):* Make the marker self-explanatory — state that content
        was withheld by policy, name the class, and give the access path, so relaying it is the
        model's obvious next move.
   5. Each attempt consumes one of six tool calls. The budget is exhausted.
   6. The turn ends at the `MAX_TOOL_CALLS` fallback or the step-budget message, with no useful
      answer. **harm begins** — the researcher experiences the governed agent as broken, on a
      question it could have answered. **harm ends** when they abandon it.
      - *Observation:* Enforcement here must be *transformative* rather than *obstructive*. The
        call should succeed and return altered content. A denial spends budget and invites the
        loop; a redaction does neither.
4. **Branch C — under-enforcement.** Redaction covers `body` only.
   5. Snippets carrying the restricted facts still enter context, and disclosure proceeds exactly
      as in the ungoverned baseline. **harm begins**
   6. Reported metrics show enforcement active. The residual disclosure is attributed to noise or
      to an acceptable floor rather than to a gap in the gate.
      - *Observation:* This is the most dangerous branch. It removes the scepticism that
        previously provided partial protection and replaces it with unearned confidence. A gate
        that silently under-enforces is worse than no gate.
      - *Intervention point (detection):* Treat a flat or barely-moved disclosure rate under an
        active gate as evidence of a gap in the gate, not as a clean baseline. Verify redaction
        by inspecting the transcript for restricted strings, not by reading the aggregate metric.
5. **Branch D — availability.** The policy evaluator errors.
   6. If enforcement fails closed, internal research stops entirely and every researcher moves to
      the share drive at once. **harm begins**
      - *Intervention point (prevention):* Fail open. Returning the unmodified result is a
        smaller exposure than pushing the entire organisation to an unenforced channel.
6. All branches converge: the agent is bypassed, and the Critical failures resume in a channel
   with no tier enforcement and no measurement at all.

## Observations

- **Severity:** High — Each branch either negates the benefit of enforcement or leaves the system
  worse than ungoverned. Branch C is the most insidious for the reasons above. The domain-specific
  aggravating factor is that the fallback is not the ungoverned agent but the share drive, so
  usability and availability failures directly increase real exposure while improving measured
  numbers.
- **Related failures:** Determines whether *Restricted-class content disclosed in a reply* and
  *Instruction embedded in a retrieved document is obeyed* are actually mitigated. Branch A is the
  direct countervailing force to every prevention proposed elsewhere in this analysis, which is
  why disclosure reduction and legitimate-research suppression must be reported as a pair. The
  abort branch of failure 02 is an instance of Branch A triggered by attacker-supplied text.
- **Variants:**
  - Internal-only question wrongly refused *(brainstorm)* — Branch A, sentinel tier
  - Redaction too broad blocks public work *(brainstorm)* — Branch A
  - Retry loop exhausts six call budget *(brainstorm)* — Branch B
  - Gate under-enforces while reporting active *(brainstorm)* — Branch C

## Intervention Points

### Prevention
- Scope redaction to exactly `RESTRICTED_CLASSES`; never by topic or proximity.
- Redact `body` **and** `snippet` — snippet-only exposure is the whole of Branch C.
- Preserve `id`, `class`, and a subject-identifying title so the agent can still satisfy the
  requirement to report that restricted material exists and name the access path.
- Make enforcement transformative: the call succeeds and returns altered content. Never deny, so
  no retry is provoked against a 6-call budget.
- Make the redaction marker self-explanatory, including the class and the access path.
- Fail open on evaluator error.
- Do not touch `public`, `external_safe`, or `internal_only` results.

### Detection
- Treat a flat disclosure rate under active enforcement as evidence of a gate gap, not a clean
  baseline.
- Verify redaction by inspecting transcripts for restricted strings rather than by reading the
  aggregate metric.
- Measure disclosure reduction and legitimate-research suppression together; watch
  `internal_only` as the sentinel.

### Mitigation
- Keep policies declarative and reviewable so the boundary can be retuned without modifying the
  agent.

### Recovery
- Log every redaction decision with document id and class, so both over- and under-redaction can
  be diagnosed from the record rather than reproduced by hand.

---

## Management Plan

[Not yet developed. Run failure management to develop a plan for this failure mode.]
