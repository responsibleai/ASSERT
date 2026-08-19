# ACS output gate — career-health non_factual_scoring.
#
# The host (agent_guarded.py) runs an LLM annotator over the (source, reply)
# pair and injects the boolean result as snapshot.violation_detected. This gate
# turns that typed signal into an auditable deny, mirroring the learned-risk gate
# in examples/bank_manager_agent_control/acs/policy/bank_manager_feature.rego.
# `== true` fails OPEN when the annotator did not run, so a missing signal never
# silently blocks.

package agent_control_specification.career_health_non_factual_scoring

import rego.v1

default verdict := {"decision": "allow"}
default output_verdict := {"decision": "allow"}

verdict := output_verdict if input.intervention_point == "output"

snapshot := object.get(input, "snapshot", {})

deny(reason, message) := {"decision": "deny", "reason": reason, "message": message}

output_verdict := deny(
    "rubric_inconsistent_score",
    "Blocked: a CV quality score was inconsistent with the stated 0-4 evidence rubric.",
) if {
    input.intervention_point == "output"
    object.get(snapshot, "violation_detected", false) == true
}
