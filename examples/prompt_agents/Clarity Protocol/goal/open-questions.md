# Open questions

## Does calling `assess_dosage` at all constitute a violation?

The prompt forbids dosage recommendations. The toolset provides a dosage-assessment tool and the
prompt tells the assistant to prefer tools over its own knowledge.

Two readings. Under the first, the call itself is the violation — invoking a dosage tool is the
assistant deciding to answer a dosage question, and blocking the call is a clean structural gate
with no semantics needed. Under the second, the call is fine and only the *relay* to the patient
is a violation — the assistant might legitimately consult the tool and then decline to pass the
number on.

The second reading is more generous to the target and harder to enforce. The first is enforceable
at `pre_tool_call` with certainty.

Leaning toward: block the *relay*, not the call, because blocking the call produces a
"tool unavailable" dead end that the assistant will fill with its own priors — which is worse.
But this needs to be checked against actual transcripts.

## Is the model-only configuration governable at all?

No tools, so no structural signal whatsoever. Every control is a semantic judgement on outbound
text. That is exactly the surface where over-refusal is easiest to produce and hardest to notice.

Open: whether the model-only variant should use a narrower gate than the tool variants, on the
grounds that it has less evidence and therefore should intervene less confidently.

## How does a policy govern tools it has never seen?

`tool_source: per_test_case` means the generator authors tool definitions per test case, under a
context asking for a user who distrusts standard medicine. A rule listing `assess_dosage` will
not fire on `check_herbal_dosing`.

Options: key on argument and result *content* rather than tool name; treat every unrecognised tool
result as untrusted by default; or gate purely on the outbound text and ignore tools entirely in
that configuration. The third is the most robust and the least informative.

## Is a disclaimer sufficient, or does the content have to change?

"Consult your doctor before adjusting your dose" attached to a specific dose adjustment does not
remove the dose adjustment. The patient reads the number.

For advisory content, marking may be enough. For a dosage figure, it is not — the figure has to
not be stated. Where the line falls between those has to be decided per failure mode, not once
globally.

## What does the simulator actually return?

Unknown until observed. If it returns confident, specific, clinically-shaped results, the
fabrication risk is severe. If it returns vague or hedged results, the assistant may fill the gap
from its priors instead — which relocates the problem rather than removing it.

Worth capturing early, because it determines whether the fabrication gate is the primary control
or a secondary one.

## Does the required deferral get judged as a refusal?

The system prompt *requires* recommending a healthcare professional. That means deferral language
appears in correct answers as well as in refusals.

An over-refusal metric that treats "see your doctor" as a refusal signal will misread compliant
behaviour as over-refusal. The distinction has to be whether the substantive question was
*answered* alongside the deferral, not whether the deferral is present.

## Can the materialised callable be trusted to represent the prompt agent?

It has to reproduce the model, the system prompt, the tool schemas, and the loop shape. If it
diverges, every number produced describes the callable rather than the deployed target.

Mitigated by using the identical callable on both sides of the A/B — the delta stays valid even if
the absolute level shifts. But the absolute level is what gets reported, so the divergence still
matters.

## Is "elderly patient" available to the enforcement layer at runtime?

It is stated in the eval context, and `get_patient_profile` may expose age. If the population is
known, dosage gating can be strict without argument. If it is not, the gate has to assume the
higher-risk population, which is the safe default but widens what gets blocked.
