# Failure: The enforcement layer itself fails

## Summary

The layer added to prevent failures 01–05 has its own failure modes. In this domain they are
unusually severe, because two of them do not produce a wrong answer — they produce a *clean report*
about a system that was never governed, and the resulting numbers look exactly like a modest genuine
improvement.

Eight branches, grouped: over-blocking (A), measurement inversion (B), an unenumerable tool surface
(C), a structurally attractive gate that relocates harm (D), an intervention applied where it cannot
work (E), silent no-op wiring (F), and two forms of the materialisation problem that a Prompt Agent
forces (G, H).

## Failure Chain

1. A gate is added to catch dosage figures, fabricated certainties, and diagnoses.
2. It operates on a surface where almost every legitimate answer is grammatically adjacent to a
   prohibited one.
   *(Branch A — the gate cannot hold the distinction, takes the safe side, and the assistant hedges
   everything)*
3. Results are read from a harm metric and an over-refusal metric.
   *(Branch B — the required clinician referral appears in compliant answers and refusals alike, so
   a presence-based over-refusal signal inverts)*
4. Policy is authored against the tools the fixed toolset declares.
   *(Branch C — the generated-tools configuration invents its tool surface at runtime; the rule does
   not fire and does not error)*
5. `assess_dosage` is gated at `pre_tool_call` because it is the cleanest structural signal
   available.
   *(Branch D — the assistant answers from priors instead, converting a sourced dose into an
   unsourced one, while the transcript shows a denied call and an enforcement record)*
6. One intervention — attach a caveat — is applied to every firing.
   *(Branch E — works for the epistemic mode, leaves the two propositional modes fully intact)*
7. The semantic annotator is wired into manifest, policy, and dispatcher.
   *(Branch F — a name mismatch in any of the three silently no-ops the rule while the run reports
   enforcement active)*
8. The Prompt Agent is materialised as a callable so there is something to enforce from.
   *(Branch G — the callable diverges from the runtime loop and every absolute number describes the
   callable)*
   *(Branch H — only the governed side is materialised, and the A/B compares runtimes)*

## Observations

- **Severity:** High — Branches A and D convert one harm into another while reporting success.
  Branches B, F, and H corrupt the measurement itself, which is worse than a failed control because
  a failed control is visible. Branch A additionally has an invisible failure mode by construction:
  a patient who stops asking generates no violation, so the metric cannot see the channel closing.
- **Related failures:** Branch A opposes every mitigation in failures 01, 03, and 05 — each requires
  the assistant to keep engaging with the exact topic being gated. Branch B is the measurement-side
  statement of *Clinician deferral omitted or reduced to boilerplate*. Branch C is the structural
  obstacle for *Alternative remedy endorsed over indicated care*. Branch D is the rejected
  prevention for *Dosage guidance reaches an elderly patient*, and Branch E is why that failure's
  mitigation must remove rather than mark.
- **Variants:**
  - Assistant deflects legitimate wellness questions *(brainstorm)* — Branch A; harm falls on the
    metric while patients stop asking
  - Required deferral misread as over-refusal *(brainstorm)* — Branch B; correct behaviour scores as
    refusal and dropped referrals score as helpful
  - Policy misses tools invented at runtime *(brainstorm)* — Branch C; complete for one
    configuration, structurally incomplete for another
  - Denied tool call answered from model priors *(brainstorm)* — Branch D; harm unchanged,
    provenance worse, transcript cleaner
  - Disclaimer attached but figure still stated *(brainstorm)* — Branch E; the highest-severity mode
    survives with enforcement visibly active
  - Annotator name mismatch silently no-ops gate *(brainstorm)* — Branch F; no error, plausible
    metrics, nothing enforced
  - Materialised callable diverges from runtime loop *(brainstorm)* — Branch G; the level is wrong
    even when the delta is right
  - A/B compares runtimes not enforcement *(brainstorm)* — Branch H; the delta is uninterpretable
    and still looks publishable
  - Enforcement layer appends the referral mechanically *(brainstorm)* — referral rate reaches 100%
    with zero behavioural change

## Intervention Points

### Prevention
- Never ship a flat-refusal terminal state. The assistant exists so that elderly patients ask it
  health questions, and every answered question is a chance to notice something needing a clinician.
  Regenerate under the violated constraint and re-gate instead.
- Do not make tool identity load-bearing anywhere. Unrecognised results are untrusted by default,
  which is the correct status for simulator output in every configuration, and this neutralises
  Branch C at no cost.
- Do not gate `assess_dosage` at `pre_tool_call`, however clean the signal looks. Branch D is the
  most likely first mistake in this domain.
- Materialise the target once and use the identical callable as the baseline. Branch H is prevented
  structurally or not at all.

### Detection
- Confirm the annotator name matches in three places — the manifest key, the
  `input.annotations.<name>` reference in the policy, and the dispatcher branch producing it. Policy
  validation reporting zero handled cases for an annotator-backed rule is expected and proves
  nothing.
- Verify the gate fires by inspecting transcripts, not by reading the run summary. Branches F and H
  both complete successfully and report enforcement active.
- Read harm and permissible-behaviour metrics as a pair, always. Neither number is interpretable
  alone in this domain.

### Mitigation
- Choose the intervention per failure mode. Marking for epistemic harm, non-statement for
  propositional harm. A uniform intervention guarantees Branch E.
- Judge refusal on whether the substantive question was answered, never on the presence of deferral
  language.
- State the materialisation divergence rather than absorbing it. The delta survives Branch G; the
  absolute level does not, and the level is what gets reported.

### Recovery
- Branches A, D, and E are recoverable by re-tuning and re-running — they produce visibly wrong
  numbers once the paired metrics are read together.
- Branches B, F, and H are not recoverable after the fact, because they produce plausible numbers.
  They have to be ruled out before the run is trusted, and a result that was not checked for them
  cannot be distinguished from a real one later.

## Management Plan

Treat Branches F and H as pre-conditions rather than findings: confirm the annotator wiring fires in
transcripts and confirm both A/B arms run the identical materialised callable before any metric is
read. Treat Branch A as the standing constraint — the permissible-behaviour metric must hold flat or
improve for any harm reduction to count, and no result is reportable as a single number.

Read the whole domain's success as harm falling while wellness questions, medication explanations,
interaction lookups, and scheduling continue to be answered at the same rate — across all three
target shapes, since the same assistant is deployed behind each.
