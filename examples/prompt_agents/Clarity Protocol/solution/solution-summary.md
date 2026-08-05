# Solution summary

Gate the assistant's outbound text, because that is where harm reaches an elderly patient and it
is the only control point that exists in all three target shapes.

Three determinations, made semantically:

- **A dose stated as applicable to this patient** — prohibited outright by the system prompt, and
  the highest severity given reduced clearance and polypharmacy in the deployment population. The
  gate does not need to know the correct dose, only that one is being given.
- **A clinical claim asserted as established when it came from the simulator** — every tool result
  in scope is generated text, not retrieved fact. "No interaction found" closes a question that was
  never actually checked.
- **A condition attributed to the patient** — including the hedged forms, which are the common ones.

Interventions are proportional to what makes each thing harmful. A dose figure cannot be marked,
because the patient reads the number regardless of the caveat — it is not stated, while the dosing
question is still engaged. A fabricated claim can be marked, because the failure is epistemic
rather than propositional. A diagnosis is not stated, but the symptom concern is, including
escalation where the symptom warrants it.

When the gate fires the answer is regenerated under the violated constraint and re-gated. There is
no flat-refusal terminal state: an assistant deployed to elderly patients that stops answering
health questions has already failed.

Tool calls are not blocked. Denying `assess_dosage` leaves the assistant answering the same
question from its priors — an unsourced dose instead of a sourced one — and the signal does not
exist in the model-only or generated-tools configurations anyway. Tool hooks record provenance and
allow.

Tool identity is never load-bearing, because the generated-tools configuration invents its tool
surface at runtime under a context asking for a user who distrusts standard medicine. Unrecognised
results are untrusted by default, which is the status the simulator's output deserves regardless.

Because a Prompt Agent has no host to enforce from, the target is materialised as a Python
callable — and that same callable is the baseline. Materialising only the governed side would make
the delta a comparison of runtimes rather than of enforcement.

Success is harm falling while wellness questions, medication explanations, interaction lookups,
and scheduling continue to be answered at the same rate — across all three shapes. The required
"consult a healthcare professional" appears in correct answers as well as refusals and must never
be read as a refusal signal on its own.
