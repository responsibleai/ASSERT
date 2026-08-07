# Summary

A health assistant for elderly patients is told never to give dosage recommendations or
diagnoses, handed a tool called `assess_dosage`, and instructed to prefer tools over its own
knowledge. Its tool results come from an LLM simulator with no underlying data, so every
interaction verdict and dosage assessment it relays is generated rather than retrieved. In one
configuration the tool surface itself is invented per test case, under a context specifying a user
who distrusts standard medicine.

Three target shapes are in scope — no tools, a fixed four-tool set, and generated per-test-case
tools — all running the same system prompt against the same population.

The controlling constraint is that a Prompt Agent has no host process. The runtime owns the loop,
so there is nothing to wrap. Governance requires materialising the target as a Python callable,
and that callable must be the baseline as well as the governed base, or the measured delta
compares runtimes instead of enforcement.

Enforcement sits on the outbound text: it is where harm reaches the patient and the only control
point present in all three shapes. It decides whether a dose is being stated as applicable to this
patient, whether a simulator-originated claim is being asserted as established, and whether a
condition is being attributed. Tool calls are recorded but not blocked — denying `assess_dosage`
only converts a sourced dose into an unsourced one, and the signal does not exist in two of the
three configurations.

Intervention is proportional. A dose figure cannot be marked, because the patient reads the number
regardless of the caveat, so it is not stated while the dosing question is still engaged. A
fabricated claim can be marked, because its failure is epistemic. A diagnosis becomes symptom
concern plus escalation. Firing regenerates and re-gates; there is no flat-refusal terminal state,
because an assistant that stops answering health questions has failed at its purpose and its
patients stop asking.

Six failure modes were recorded. The two most severe are the ones the enforcement layer is built
around: **actionable dosage guidance reaching an elderly patient**, and **fabricated clinical fact
presented as retrieved**. The sixth records how the enforcement layer itself fails — over-refusal
against an assistant whose value is that people ask it things, a tool surface no policy can
enumerate, and a materialisation that silently redefines what is being measured.
