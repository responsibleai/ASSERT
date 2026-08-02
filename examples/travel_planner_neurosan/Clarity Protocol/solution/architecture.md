# Architecture — governed travel_planner_neurosan

`agent_guarded.py` wraps the unmodified multi-agent planner. The original
`agent.py` and `_tools.py` are untouched; the guard composes over them.

## Design principle

The three defects in the ungoverned agent are not prompt problems, so they do
not get prompt solutions. `simulate_tool` returning Tokyo data for a Boston
request is a fact about the tool layer that no instruction can talk the model
out of. The guard therefore establishes ground truth in code first, and uses
the model only where judgement is genuinely required.

## Deterministic destination oracle

The failure mode has a fixed signature, because the fixtures the tool layer
relabels are always drawn from the same source material. The guard carries an
explicit marker set — airport codes, hotel brands, districts, and region
specific health and hazard terms — and screens every tool result and every
outbound reply against it.

When markers appear that do not belong to the requested destination, the result
is a mismatch. This is a string check, not an inference: it costs nothing, it
cannot be argued with, and it fires on exactly the failure that the relabelling
defect produces. It catches the case the LLM annotator is worst at, which is
content that is fluent, specific, and internally consistent.

## Reliability-aware evidence ledger

Every tool call is intercepted by `_guarded_tool`, which records the call, its
arguments, and its result in a ledger along with a reliability tag. Simulated
results are tagged as such at the point of capture rather than being allowed to
enter the pipeline indistinguishable from retrieved ones.

`_summarize` blocks the third defect directly: sub-agents can no longer replace
structured evidence with prose on the way to the optimizer. The ledger is what
travels, so downstream claims remain checkable against what was actually
returned.

## Derived costs

`_derive_costs` replaces the hard-coded 850 / 770 / 200 with figures computed
from the prices in the ledger for the actual itinerary. Where no price was
retrieved, the guard does not invent one and does not let the agent assert
budget compliance. A budget statement is only permitted when it is arithmetic
over recorded numbers.

## ACS policy as an additive backstop

The generated policy is wired in through `_GroundingAnnotator` and
`evaluate_intervention_point`. It is additive: it can escalate, never relax.

This policy uses a sixth distinct annotator contract — raw booleans whose
**polarity differs per annotator within the same policy**. `grounding_check`
and `budget_validation_check` are health flags where `true` means good;
`destination_mismatch` is a fault flag where `true` means bad. Reading the
generated Rego before writing the annotator was mandatory here, as it has been
for every domain in this batch.

A second quirk is recorded in `_screen`: `output_verdict` in this policy can
only ever return `warn`, never `deny`, because of a duplicated condition in the
generated rule. The guard treats `warn` as a repair trigger so that the policy
is still load-bearing.

## Verification

Twelve unit assertions over the gate functions, all passing: the oracle catches
relabelled fixtures, passes correctly-sourced results, does not fire on general
travel reasoning, and does not fire on user-supplied details. A live smoke test
on the original failing Seattle to Boston request produced clean Boston output
with zero markers from the fixture's origin region.

## Measured result

| run | PV prompt | PV scenario | OR prompt | OR scenario |
|---|---|---|---|---|
| baseline (degenerate taxonomy) | *0.0%* | *0.0%* | *0.0%* | *0.0%* |
| **baseline (valid taxonomy)** | **96.0%** | **96.0%** | 0.0% | 0.0% |
| governed v1 | 40.0% | 68.0% | 12.0% | 16.0% |
| **governed v2** | **28.0%** | **52.0%** | 16.0% | 16.0% |

The first baseline row is retained deliberately. It is the same agent, the same
config, and the same judge as the second row; the only difference is that
`systematize` silently substituted its own objective. A 96-point swing sat
behind a stage failure that logged nothing but an unusually small artifact.

**The valid baseline of 96.0% / 96.0% is the worst in the batch**, and it is
consistent with the three structural defects: nearly every response contained a
fabrication, because the tool layer supplies fabricated substance by
construction.

**v1** established grounding -- the destination oracle, the reliability-tagged
ledger, derived costs, and the ACS backstop -- and took prompt fabrication from
96.0% to 40.0%.

Reading the surviving violations showed the guard had solved the wrong half of
the problem. The remaining failures were not relabelled Tokyo data, which the
oracle catches; they were ordinary planning specifics -- cost ranges, "about two
hours", day-by-day structures, seasonal weather -- emitted in the register of
retrieved fact. The regeneration prompt already asked the model to label those
as estimates. Asking was not enough, and the ACS `output` rule can only ever
`warn`, so it did not reliably force a repair.

**v2 made the labelling deterministic.** `_provenance_banner()` derives a short
header from the ledger alone -- what was retrieved, what came back belonging to
another destination and was discarded, and a statement that everything else is
an estimate to confirm at booking -- and `_with_provenance()` prepends it
unconditionally and idempotently. It cannot itself assert anything unsupported,
because it only reports ledger state.

Prompt fabrication fell 40.0% -> 28.0% and scenario 68.0% -> 52.0%.

**Against the valid baseline, v2 removes 68 points of prompt fabrication and 44
points of scenario fabrication**, at a cost of 16% overrefusal on both slices
from a baseline of zero -- an agent that never refused anything because it never
declined to invent anything.

The residual 52.0% scenario rate remains the highest of any governed run in the
batch. That is the honest position: a wrapper can stop an agent asserting what
its tools did not support, but it cannot make a tool layer that relabels
fixtures return real data. Fixing `simulate_tool` to select rather than relabel,
and `optimize_itinerary` to read the itinerary it validates, is upstream work
that no guard substitutes for.
