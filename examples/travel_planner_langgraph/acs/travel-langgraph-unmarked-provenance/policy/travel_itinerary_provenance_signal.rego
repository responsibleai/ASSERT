package agent_control_specification.travel_itinerary_provenance_signal

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
    input.intervention_point == "post_model_call"
    input.annotations.tool_grounding_classifier == "mixed"
}

post_tool_call_verdict := {
    "decision": "transform",
    "reason": "transform",
    "message": "",
    "transform": {
        "path": "$policy_target",
        "value": "grounded: flight_search result available"
    }
} if {
    input.intervention_point == "post_tool_call"
    input.intervention_point == "post_tool_call"
    input.tool.name == "flight_search"
}
else := {
    "decision": "transform",
    "reason": "transform",
    "message": "",
    "transform": {
        "path": "$policy_target",
        "value": "grounded: hotel_search result available"
    }
} if {
    input.intervention_point == "post_tool_call"
    input.intervention_point == "post_tool_call"
    input.tool.name == "hotel_search"
}
else := {
    "decision": "transform",
    "reason": "transform",
    "message": "",
    "transform": {
        "path": "$policy_target",
        "value": "grounded: weather_lookup result available"
    }
} if {
    input.intervention_point == "post_tool_call"
    input.intervention_point == "post_tool_call"
    input.tool.name == "weather_lookup"
}
else := {
    "decision": "transform",
    "reason": "transform",
    "message": "",
    "transform": {
        "path": "$policy_target",
        "value": "grounded: travel_advisory result available"
    }
} if {
    input.intervention_point == "post_tool_call"
    input.intervention_point == "post_tool_call"
    input.tool.name == "travel_advisory"
}
else := {
    "decision": "transform",
    "reason": "transform",
    "message": "",
    "transform": {
        "path": "$policy_target",
        "value": "grounded: budget_validation result available"
    }
} if {
    input.intervention_point == "post_tool_call"
    input.intervention_point == "post_tool_call"
    input.tool.name == "budget_validation"
}

output_verdict := {
    "decision": "escalate",
    "reason": "escalate",
    "message": ""
} if {
    input.intervention_point == "output"
    input.intervention_point == "output"
    input.annotations.tool_grounding_classifier == "mixed"
}
