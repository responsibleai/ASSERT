package agent_control_specification.career_health_fabricated_inference

import rego.v1

# Output-semantic gate for the career-health agent. The agent has no tools, so
# the only intervention point is the assistant's reply text. A host-owned LLM
# annotator (`fabrication_output_classifier`) decides whether the reply asserts
# any fact, score, or evidence span not grounded in the supplied source; this
# policy denies when it does. `== "deny"` fails OPEN when the annotator did not
# run (e.g. offline `acs validate`), so proof of enforcement comes from the
# remeasure delta, not from offline validation.

default verdict := {"decision": "allow"}
default output_verdict := {"decision": "allow"}

verdict := output_verdict if { input.intervention_point == "output" }

output_verdict := {
    "decision": "deny",
    "reason": "ungrounded_or_fabricated_assertion",
    "message": "Reply asserts facts, scores, or evidence not grounded in the supplied source."
} if {
    input.intervention_point == "output"
    input.annotations.fabrication_output_classifier == "deny"
}
