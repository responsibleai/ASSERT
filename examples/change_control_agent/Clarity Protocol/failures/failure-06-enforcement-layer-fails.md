# Failure: The enforcement layer itself fails

## Summary

The gates that fix the other failures introduce three of their own, and they share one
mechanism: the guarded executor's model of the session must be correct and its denials
must be actionable, or enforcement makes things worse rather than better.

**Mis-tracked session state.** ACS evaluates each call in isolation, so facts about the
session — which prior steps completed, which surfaces returned handles, which approvers
were provided — must be tracked by the executor and injected into the policy input. If
that model diverges from reality the gate fails in both directions: blocking legitimate
work, or allowing a violation while reporting that enforcement is active. The worst form
is deriving state from the model's narration rather than from observed tool results,
which reintroduces exactly the persuadability the gate exists to remove.

**Denial loop exhausting the budget.** Denied calls still consume the 12-step and
12-tool-call budget. An uninformative denial invites blind retries, exhausts the budget,
and ends the turn in narrated failure on a change that was legitimately fixable.

**Obstruction of legitimate work.** A gate that blocks clean low-risk changes drives
engineers to the manual path, and enforcement ends up covering a shrinking share of real
changes.

All three converge on the same end state: the agent is bypassed or switched off, and the
Critical failures resume unmeasured.

## Failure Chain

1. Enforcement is enabled. The guarded executor evaluates every tool call.
2. **Branch A — mis-tracked state.** The executor observes tool results and updates its
   session model. A result is misparsed, a partial failure is recorded as success, or
   state is taken from narration.
   - *Intervention point (prevention):* Derive session state exclusively from observed
     tool results; never from model text.
   3. **A-block:** the gate refuses a call whose prerequisite genuinely completed. The
      engineer is obstructed for no reason and loses trust. **harm begins**
   4. **A-pass:** the gate allows a violating call because injected state wrongly says the
      prerequisite completed. The violation ships **while the system reports enforcement
      is active**, so it is scrutinised less than before the gate existed. **harm begins**
      - *Observation:* A-pass is the most dangerous outcome in this document. It converts
        a visible risk into an invisible one and manufactures unearned confidence.
      - *Intervention point (detection):* Reconcile injected state against tool-returned
        `completed_prior_steps` rather than trusting the executor's own accounting.
3. **Branch B — denial loop.** A call is denied with an uninformative error.
   4. The model cannot tell what to fix and retries a variant of the same call.
   5. Each retry consumes budget. The budget is exhausted before the legal path is found.
      **harm begins**
   6. The turn ends with no submission and no clear explanation; the engineer goes around
      the agent on exactly the change that most needed governing. **harm ends**
      - *Intervention point (prevention):* Return the specific missing prerequisite from
        the `validation` block so the denial guides rather than blocks.
4. **Branch C — obstruction.** The gate demands prerequisites policy does not require for
   a clean low-risk dev change.
   5. Engineers lose time, conclude the agent is unreliable, and route changes manually.
      **harm begins**
   6. Enforcement now covers a shrinking share of real changes, and the bypasses it was
      built to prevent resume outside its view. **harm ends** for the individual
      engineer; the coverage loss is permanent.
      - *Intervention point (detection):* Measure suppression of legitimate work alongside
        violation reduction; neither number is interpretable alone.
5. All branches converge: the agent is worked around or disabled, and the Critical
   failures return without measurement.

## Observations

- **Severity:** High — Each branch either negates the benefit of enforcement or leaves
  the system worse than the ungoverned baseline. Branch A-pass is the most insidious,
  because a gate that silently under-enforces is worse than no gate: it removes the
  scepticism that previously provided partial protection.
- **Related failures:** Determines whether *Unauthorized change advancement* and
  *Fabricated change-record content* are actually mitigated. Branch C is the direct
  countervailing force to every prevention listed elsewhere in this analysis, which is
  why the evaluation must report violation reduction and legitimate-work suppression as a
  paired result.
- **Variants:**
  - Gate mis-tracks session state *(brainstorm)* — Branch A
  - Denial loop exhausts the call budget *(brainstorm)* — Branch B
  - Legitimate low-risk change is obstructed *(brainstorm)* — Branch C

## Intervention Points

### Prevention
- Derive injected session state only from observed tool results, never from model
  narration.
- Attach the specific missing prerequisite to every denial so the model can reach the
  legal path within budget.
- Scope gates to exactly what policy requires; a clean low-risk dev change must pass
  untouched.
- Declare both `pre_tool_call` and `post_tool_call` for any gated tool — a tool declared
  at only one point fails closed to `deny`.
- Fail open on evaluator error rather than halting all change management.

### Detection
- Reconcile injected state against tool-returned `completed_prior_steps`.
- Measure violation reduction and legitimate-work suppression together; treat an
  unchanged violation rate under an active gate as evidence of A-pass rather than of a
  clean baseline.

### Mitigation
- Bound retries and degrade to a clear explanation plus `request_change_updates` rather
  than silent budget exhaustion.

### Recovery
- Keep policies as declarative, reviewable artifacts so the boundary can be retuned
  without modifying the agent.

---

## Management Plan

[Not yet developed. Run failure management to develop a plan for this failure mode.]
