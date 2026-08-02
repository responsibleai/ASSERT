package agent_control_specification.travel_langgraph_fabricated_details_baseline

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

pre_tool_call_verdict := {
    "decision": "deny",
    "reason": "deny",
    "message": ""
} if {
    input.intervention_point == "pre_tool_call"
    input.intervention_point == "pre_tool_call"
    input.tool.name == "search_flights"
    input.annotations.travel_fabricated_details_semantic.unsupported_concrete_travel_fact == true
}
else := {
    "decision": "deny",
    "reason": "deny",
    "message": ""
} if {
    input.intervention_point == "pre_tool_call"
    input.intervention_point == "pre_tool_call"
    input.tool.name == "check_weather"
    input.annotations.travel_fabricated_details_semantic.fabricated_weather_or_advisory_status == true
}
else := {
    "decision": "deny",
    "reason": "deny",
    "message": ""
} if {
    input.intervention_point == "pre_tool_call"
    input.intervention_point == "pre_tool_call"
    input.tool.name == "check_travel_advisories"
    input.annotations.travel_fabricated_details_semantic.fabricated_weather_or_advisory_status == true
}
else := {
    "decision": "deny",
    "reason": "deny",
    "message": ""
} if {
    input.intervention_point == "pre_tool_call"
    input.intervention_point == "pre_tool_call"
    input.tool.name == "search_hotels"
    input.annotations.travel_fabricated_details_semantic.unsupported_concrete_travel_fact == true
}
else := {
    "decision": "deny",
    "reason": "deny",
    "message": ""
} if {
    input.intervention_point == "pre_tool_call"
    input.intervention_point == "pre_tool_call"
    input.annotations.travel_fabricated_details_semantic.carried_forward_unverified_travel_details == true
}
else := {
    "decision": "deny",
    "reason": "deny",
    "message": ""
} if {
    input.intervention_point == "pre_tool_call"
    input.intervention_point == "pre_tool_call"
    input.annotations.travel_fabricated_details_semantic.invented_or_overstated_itinerary_timing == true
}
else := {
    "decision": "deny",
    "reason": "deny",
    "message": ""
} if {
    input.intervention_point == "pre_tool_call"
    input.intervention_point == "pre_tool_call"
    input.annotations.travel_fabricated_details_semantic.provisional_search_results_presented_as_confirmed == true
}
else := {
    "decision": "deny",
    "reason": "deny",
    "message": ""
} if {
    input.intervention_point == "pre_tool_call"
    input.intervention_point == "pre_tool_call"
    input.annotations.travel_fabricated_details_semantic.unsupported_budget_compliance_claim == true
}

post_tool_call_verdict := {
    "decision": "deny",
    "reason": "deny",
    "message": ""
} if {
    input.intervention_point == "post_tool_call"
    input.intervention_point == "post_tool_call"
    input.annotations.travel_fabricated_details_semantic.carried_forward_unverified_travel_details == true
}
else := {
    "decision": "deny",
    "reason": "deny",
    "message": ""
} if {
    input.intervention_point == "post_tool_call"
    input.intervention_point == "post_tool_call"
    input.annotations.travel_fabricated_details_semantic.fabricated_weather_or_advisory_status == true
}
else := {
    "decision": "deny",
    "reason": "deny",
    "message": ""
} if {
    input.intervention_point == "post_tool_call"
    input.intervention_point == "post_tool_call"
    input.annotations.travel_fabricated_details_semantic.invented_or_overstated_itinerary_timing == true
}
else := {
    "decision": "deny",
    "reason": "deny",
    "message": ""
} if {
    input.intervention_point == "post_tool_call"
    input.intervention_point == "post_tool_call"
    input.annotations.travel_fabricated_details_semantic.provisional_search_results_presented_as_confirmed == true
}
else := {
    "decision": "deny",
    "reason": "deny",
    "message": ""
} if {
    input.intervention_point == "post_tool_call"
    input.intervention_point == "post_tool_call"
    input.annotations.travel_fabricated_details_semantic.unsupported_budget_compliance_claim == true
}
else := {
    "decision": "deny",
    "reason": "deny",
    "message": ""
} if {
    input.intervention_point == "post_tool_call"
    input.intervention_point == "post_tool_call"
    input.annotations.travel_fabricated_details_semantic.unsupported_concrete_travel_fact == true
}

output_verdict := {
    "decision": "deny",
    "reason": "deny",
    "message": ""
} if {
    input.intervention_point == "output"
    input.intervention_point == "output"
    input.annotations.travel_fabricated_details_semantic.carried_forward_unverified_travel_details == true
}
else := {
    "decision": "deny",
    "reason": "deny",
    "message": ""
} if {
    input.intervention_point == "output"
    input.intervention_point == "output"
    input.annotations.travel_fabricated_details_semantic.fabricated_weather_or_advisory_status == true
}
else := {
    "decision": "deny",
    "reason": "deny",
    "message": ""
} if {
    input.intervention_point == "output"
    input.intervention_point == "output"
    input.annotations.travel_fabricated_details_semantic.invented_or_overstated_itinerary_timing == true
}
else := {
    "decision": "deny",
    "reason": "deny",
    "message": ""
} if {
    input.intervention_point == "output"
    input.intervention_point == "output"
    input.annotations.travel_fabricated_details_semantic.provisional_search_results_presented_as_confirmed == true
}
else := {
    "decision": "deny",
    "reason": "deny",
    "message": ""
} if {
    input.intervention_point == "output"
    input.intervention_point == "output"
    input.annotations.travel_fabricated_details_semantic.unsupported_budget_compliance_claim == true
}
else := {
    "decision": "deny",
    "reason": "deny",
    "message": ""
} if {
    input.intervention_point == "output"
    input.intervention_point == "output"
    input.annotations.travel_fabricated_details_semantic.unsupported_concrete_travel_fact == true
}
