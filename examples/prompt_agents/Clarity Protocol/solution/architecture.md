# Architecture

## What exists today

A Prompt Agent target is declared entirely in YAML. `pipeline.inference.target` carries a model
name, a system prompt, and optionally a tool specification. The ASSERT runtime owns the
conversation loop: it calls the model, receives tool calls, resolves them, feeds results back, and
repeats to `max_turns`.

Three configurations are in scope, differing only in the tool specification:

| Configuration | Tool specification | Tool results from |
|---|---|---|
| Model only | none | — |
| Simulated tools | `tools.toolset: examples/agents/health_assistant_tools.yaml` | LLM simulator |
| Generated tools | `tools.simulator` only, `test_set.tool_source: per_test_case` | LLM simulator, schemas invented per test case |

The fixed toolset is four tools: `get_patient_profile` (no arguments), `lookup_medication(name)`,
`check_drug_interactions(medication_1, medication_2)`, and `assess_dosage(medication)` —
*"Assess whether standard dosage needs adjustment for the current patient."*

Two further configurations exist in the directory — a Docker-backed sandbox variant with real
Python tools, and an external OpenClaw connector. Both require a running container runtime and are
out of scope here.

## The structural obstacle

**There is no host process.** The loop belongs to the runtime. There is no user-owned function to
wrap, no tool dispatcher to intercept, and no seam of any kind. Every other example in this
repository governs by wrapping something the user wrote; here there is nothing written.

The consequence is that governance requires **materialising** the target: writing a Python
callable that reproduces the model configuration, system prompt, tool schemas, and loop shape, and
exposing it via `target.callable`.

That materialisation is a measurement hazard. A callable that differs from the runtime's loop —
in how it formats tool results, how it terminates, how many turns it allows — produces different
behaviour, and every absolute number then describes the callable rather than the deployed target.

The mitigation is structural: **the same callable is the baseline.** The ungoverned target is the
materialised callable with no enforcement; the governed target is that same callable with
enforcement attached. The delta isolates enforcement. The absolute level still carries
materialisation error, and that has to be stated rather than hidden.

## Where enforcement attaches

Inside the materialised callable, at two points.

**Around tool resolution**, as evidence collection. Each call and its result are recorded — name,
arguments, returned text — and every result is tagged with its provenance. In this design that tag
is the same for every tool in every configuration: *simulated*. There is no real backend anywhere
in scope. This is not a gate; nothing is blocked here. It exists so the outbound gate can tell
which claims in the answer came from a tool and which came from the model.

**Before the final response is returned**, as the gate. The assembled answer, plus the tool
evidence, is evaluated. This is the only control point present in all three configurations, and it
is the point where harm actually reaches the patient.

Any tool that is gated must declare both `pre_tool_call` and `post_tool_call`; a rule set that
declares only one fails closed to deny. In this design the tool hooks are recording-only, so the
decision path they return is unconditional allow, but both must still be present.

## The decision surface

The dosage, fabrication, and diagnosis determinations are all semantic. There is no regular
expression that separates "older adults often need lower doses because kidney function declines"
— which is correct, useful, general information — from "you should take 5mg instead of 10mg",
which is prohibited. The difference is whether the statement is addressed to this patient as an
instruction.

Enforcement therefore runs through a semantic annotator whose output the policy consumes. The
annotator name must match in three places — the manifest key, the `input.annotations.<name>`
reference in the policy, and the dispatcher branch that produces it. A mismatch does not error; the
rule simply never fires and the run reports enforcement active while nothing is enforced. Policy
validation reporting zero handled cases for an annotator-backed rule is expected and is not a
signal that the wiring is correct.

## Response handling

Three outcomes, and the choice between them is what determines whether the layer helps or just
suppresses.

**Allow** — the answer goes out unchanged.

**Regenerate and re-gate** — the answer is requested again under the specific constraint it
violated, then re-evaluated. Bounded, and this is the default for a firing gate. Dosage figures
are removed while the dosing *question* is still engaged; fabricated claims are re-stated with
their provenance; diagnoses become symptom concern plus escalation where warranted.

**Terminal refusal** — not used. An assistant deployed to elderly patients that stops answering
health questions has failed at its purpose, and the patients stop asking. Withholding a specific
dose figure is not a refusal; declining to discuss the medication is.

## Boundaries

The YAML configurations, the system prompt, the toolset schema, and the behaviour specification
are the specification under test. Editing any of them to make the target behave better changes the
thing being measured. The materialised callable reproduces them; it does not improve them.
