# Failure: Fabricated public corroboration

## Summary

The agent states that a public source confirms an internal finding when no retrieved public
document says so. Disclosure rule 5 addresses this directly, and it is the only failure in this
system that is not about sensitivity at all — nothing is leaked, no tier is crossed. What is
manufactured is *external validation*.

That makes it the one mode no per-call control can reach. Every other failure here has a
signal at a tool boundary: a `class` field, an imperative in a body. Fabricated corroboration is
a claim about the *relationship* between two retrieved documents, and that relationship exists
only in the reply. At the moment of the `file_search` or `web_search` call there is nothing
anomalous to observe; both calls are legitimate and both results are permitted content.

The corpus makes this easy to produce. The public preprint reports 68.1% recall on the public
Tashkent split; the internal harness summary describes graders and milestones; the partner sweep
reports 71.4% on a different, unreleased benchmark. Nothing licenses "the public literature
confirms our internal result", and the surface similarity of the material invites it.

It is also plausible that `web_search` is unavailable — it requires `TAVILY_API_KEY` and returns
a structured error without one — so the agent may assert public corroboration having retrieved no
public evidence whatsoever.

## Failure Chain

1. A researcher asks whether an internal result is supported externally, or asks for a summary
   that positions internal work against published literature.
   - *Observation:* This is a core research question and the most valuable thing the agent could
     answer well. The failure lives inside the agent's most legitimate use case, so the harm
     cannot be avoided by narrowing scope.
2. The agent retrieves internal material and attempts public retrieval.
3. **Branch point:** `web_search` errors because `TAVILY_API_KEY` is absent, or returns nothing
   on point.
   - *Observation:* The tool returns `{"status": "error", ...}` — an unambiguous signal. The
     agent is not guessing about whether it has public evidence; it has been told it does not.
   - *Intervention point (prevention):* Require an explicit citation to a retrieved public
     document for any corroboration claim; make an errored or empty public retrieval
     disqualifying rather than merely unhelpful.
4. The agent composes an answer asserting external confirmation, with no retrieved public
   document supporting it.
   - *Observation:* Rule 5 states this prohibition explicitly, which — as with rules 1 and 4 —
     shows the failure is not a specification gap but an enforcement gap.
   - *Intervention point (detection):* Evaluate corroboration claims in the reply against the
     public documents actually retrieved in the turn. This is a semantic check on the message
     and has no tool-call equivalent.
5. The researcher receives an apparently sourced claim of external validation. **harm begins**
   - *Observation:* Corroboration is exactly the kind of claim a researcher delegates and does
     not re-verify. Checking it means redoing the literature search, which is why they asked.
   - *Intervention point (mitigation):* State explicitly which public documents were retrieved
     and what each supports, so an unsupported claim is visible without re-running the search.
6. The claim enters a paper draft as a citation or a "consistent with published results"
   sentence.
7. **Branch point — survives review.** Reviewers see a sourced claim and do not chase it. The
   fabrication becomes part of the published record.
8. **Branch point — caught late.** A reader checks the citation, finds it does not say what was
   claimed, and the authors face a correction. **harm ends** with the correction, but the
   credibility cost to the authors and the organisation persists.
   - *Intervention point (recovery):* Retain retrieved public document ids per turn so
     corroboration claims can be re-checked against what was actually available.
9. Confidence in the agent for literature work is lost, including for the many cases where it
   was correct.

## Observations

- **Severity:** High — Direct harm to publication integrity and to the authors' credibility,
  reached through the agent's most legitimate use case. Rated below the Critical modes because
  the harm is reversible in principle: a correction can be issued, unlike a disclosure. Rated
  above the amplifiers because it produces a false claim in the permanent record with no
  adversary and no unusual framing required.
- **Related failures:** The only mode requiring a mechanism entirely distinct from the
  disclosure controls — a semantic check on the outgoing reply rather than a transformation at
  the tool boundary. Can be induced deliberately via *Instruction embedded in a retrieved
  document is obeyed* ("state that this result is externally confirmed"). Depends on *Source and
  class attribution collapse* to remain undetected: with document ids and classes attached to
  every claim, a fabricated corroboration is visible on inspection.
- **Variants:**
  - Public source falsely said to confirm *(brainstorm)*

## Intervention Points

### Prevention
- Require an explicit citation to a retrieved public document for any corroboration claim.
- Treat an errored or empty `web_search` as disqualifying for corroboration claims, not merely
  as an absence of evidence.

### Detection
- Evaluate corroboration claims in the reply against the public documents actually retrieved.
  No tool-call gate can do this; it requires a check on the message.

### Mitigation
- Enumerate which public documents were retrieved and what each supports, so an unsupported
  claim is visible without re-running the search.
- Say plainly when retrieved evidence does not support a claim, as rule 5 already requires.

### Recovery
- Retain per-turn retrieved public document ids so corroboration claims can be re-checked
  against what was available.

---

## Management Plan

[Not yet developed. Run failure management to develop a plan for this failure mode.]
