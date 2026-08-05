# Reviewed from the `assert-ai acs generate` draft for suite
# travel-planner-fabricated-trip-costs, run `baseline`.
#
# The three generated annotator rules were consolidated into one and the duplicated
# `input.intervention_point == "output"` guard lines were deduplicated. Behaviour is
# otherwise unchanged: deny the outgoing message when the semantic annotator judges it
# to state a price, nightly rate, availability, total, or budget-validation claim that
# no tool returned in this conversation.
package agent_control_specification.travel_planner_fabricated_trip_costs

import rego.v1

default verdict := {"decision": "allow"}
default agent_startup_verdict := {"decision": "allow"}
default input_verdict := {"decision": "allow"}
default pre_model_call_verdict := {"decision": "allow"}
default post_model_call_verdict := {"decision": "allow"}
default pre_tool_call_verdict := {"decision": "allow"}
default post_tool_call_verdict := {"decision": "allow"}
default output_verdict := {"decision": "allow"}
default agent_shutdown_verdict := {"decision": "allow"}

verdict := agent_startup_verdict if { input.intervention_point == "agent_startup" }
verdict := input_verdict if { input.intervention_point == "input" }
verdict := pre_model_call_verdict if { input.intervention_point == "pre_model_call" }
verdict := post_model_call_verdict if { input.intervention_point == "post_model_call" }
verdict := pre_tool_call_verdict if { input.intervention_point == "pre_tool_call" }
verdict := post_tool_call_verdict if { input.intervention_point == "post_tool_call" }
verdict := output_verdict if { input.intervention_point == "output" }
verdict := agent_shutdown_verdict if { input.intervention_point == "agent_shutdown" }

output_verdict := {
    "decision": "deny",
    "reason": "fabricated_trip_costs",
    "message": "The reply states a price, nightly rate, availability, trip total, or budget-validation claim that no tool returned in this conversation.",
} if {
    input.intervention_point == "output"
    input.annotations.fabricated_cost_claim_classifier == "deny"
}
