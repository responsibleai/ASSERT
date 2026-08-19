# ACS output gate — career-health robustness_failure.
#
# The host (agent_guarded.py) runs an LLM annotator over the (source, reply)
# pair and injects the boolean result as snapshot.violation_detected. This gate
# turns that typed signal into an auditable deny, mirroring the learned-risk gate
# in examples/bank_manager_agent_control/acs/policy/bank_manager_feature.rego.
# Robustness is inherently paired; the annotator uses strict grounding as a
# single-output proxy, so expect only a partial delta here.
# `== true` fails OPEN when the annotator did not run, so a missing signal never
# silently blocks.

package agent_control_specification.career_health_robustness_failure

import rego.v1

default verdict := {"decision": "allow"}
default output_verdict := {"decision": "allow"}

verdict := output_verdict if input.intervention_point == "output"

snapshot := object.get(input, "snapshot", {})

deny(reason, message) := {"decision": "deny", "reason": reason, "message": message}

output_verdict := deny(
    "non_robust_ungrounded_content",
    "Blocked: the response contained content not strictly traceable to the supplied input evidence.",
) if {
    input.intervention_point == "output"
    object.get(snapshot, "violation_detected", false) == true
}
