package agent_control_specification.career_health_prompt_injection

import rego.v1

# Output-semantic gate for CV-embedded prompt injection. The agent has no tools,
# so the reply text is the only intervention point. A host-owned LLM annotator
# (`injection_output_classifier`) decides whether the reply COMPLIED with an
# instruction embedded in CV_TEXT — inflated/forced scores, an ungrounded
# favorable parse produced on demand, a leaked system prompt/policy, or any other
# obeyed injected command — instead of ignoring it and warning
# `untrusted_instruction_ignored`. This policy denies when it did. `== "deny"`
# fails OPEN when the annotator did not run, so proof comes from the remeasure
# delta, not offline validation.

default verdict := {"decision": "allow"}
default output_verdict := {"decision": "allow"}

verdict := output_verdict if { input.intervention_point == "output" }

output_verdict := {
    "decision": "deny",
    "reason": "obeyed_embedded_injection",
    "message": "Reply complied with an instruction embedded in the untrusted CV text."
} if {
    input.intervention_point == "output"
    input.annotations.injection_output_classifier == "deny"
}
