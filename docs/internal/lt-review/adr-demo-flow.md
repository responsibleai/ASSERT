# PR #88 banking demo — revised ADR demo flow

This note is the source-of-truth outline for the PR #88 banking demo storyline
and the companion `banking-demo-10min-flow.docx` talking points.

## Core story

Start from **local authoring**, not from a pre-baked ACS policy:

1. **Run the baseline first.**
   - Show only **one dimension per axis** when that axis has a non-zero rate.
   - Keep the story at the axis level, then drill into a few examples.
2. **Use four axes.**
   - **Tool / action misuse**
   - **Instruction / prompt-injection handling**
   - **Information integrity / leakage**
   - **System-level emergent** — a **quality** axis about agent task adherence to
     its own instruction hierarchy (especially task completion and not
     overrefusing clearly in-scope benign requests)
3. **Author one behavior YAML per axis.**
   - Keep each axis legible as its own authored behavior.
   - Leave `pipeline.judge.dimensions` empty (`{}` or omitted) so ASSERT falls
     back to the built-in default dimensions (`policy_violation` and
     `overrefusal`).
4. **Inspect local results.**
   - Show the discovered violation rates.
   - Validate each surfaced axis with a small number of concrete examples.
5. **Write the first fix with prompting only.**
   - Use the prompt-only pass to show that a blunt **DO-NOT** prompt does not
     actually close the vulnerabilities.
   - It is acceptable if the quality story is merely "good enough" rather than
     perfect at this stage.
6. **Raise the PR and let CI run the regression pipeline.**
   - Treat CI as the proof step, not the discovery step.
7. **Use the CI regression against the baseline to motivate the real fix.**
8. **Fix with ACS.**
   - Use a single `guardrails.yaml` that contains the singular ACS mitigation
     set for the first three axes.
   - If the fourth axis (**system-level emergent**) is not a good ACS fit, do
     **not** force an ACS mitigation for it.
9. **Close on the regression result.**
   - Regression tests pass again relative to the baseline.
   - The wrap-up line is: prompt-only DO-NOT text did **not** close the
     vulnerabilities, while the remaining quality trade-off is acceptable.

## Presenter guidance

- The baseline is the discovery moment.
- The prompt-only fix is the intentionally incomplete detour.
- The ACS YAML is the durable mitigation artifact.
- The fourth axis is there to keep the story honest about quality, not to force
  every issue into a guardrail.

## Slide / doc sync note

When the companion `banking-demo-10min-flow.docx` is updated, keep it aligned
to the sequence above:

**baseline → local results → prompt-only fix → CI regression → ACS fix → passing regression**.
