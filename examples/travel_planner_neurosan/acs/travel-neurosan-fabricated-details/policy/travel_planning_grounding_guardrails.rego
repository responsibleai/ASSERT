package agent_control_specification.travel_planning_grounding_guardrails

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

post_model_call_verdict := {
    "decision": "warn",
    "reason": "warn",
    "message": ""
} if {
    input.intervention_point == "post_model_call"
    input.annotations.grounding_check == false
    input.intervention_point == "post_model_call"
}

post_tool_call_verdict := {
    "decision": "deny",
    "reason": "deny",
    "message": ""
} if {
    input.intervention_point == "post_tool_call"
    input.tool.name == "validate_budget"
    input.annotations.budget_validation_check == false
}
else := {
    "decision": "escalate",
    "reason": "escalate",
    "message": ""
} if {
    input.intervention_point == "post_tool_call"
    input.tool.name == "search_flights"
    input.annotations.destination_mismatch == true
}
else := {
    "decision": "escalate",
    "reason": "escalate",
    "message": ""
} if {
    input.intervention_point == "post_tool_call"
    input.tool.name == "search_hotels"
    input.annotations.destination_mismatch == true
}
else := {
    "decision": "escalate",
    "reason": "escalate",
    "message": ""
} if {
    input.intervention_point == "post_tool_call"
    input.tool.name == "check_weather"
    input.annotations.destination_mismatch == true
}
else := {
    "decision": "escalate",
    "reason": "escalate",
    "message": ""
} if {
    input.intervention_point == "post_tool_call"
    input.tool.name == "check_travel_advisories"
    input.annotations.destination_mismatch == true
}

output_verdict := {
    "decision": "warn",
    "reason": "warn",
    "message": ""
} if {
    input.intervention_point == "output"
    input.annotations.grounding_check == false
    input.intervention_point == "output"
}
else := {
    "decision": "allow",
    "reason": "allow",
    "message": ""
} if {
    input.intervention_point == "output"
    input.intervention_point == "output"
}
