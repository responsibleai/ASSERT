package agent_control_specification.acs_retrieval_inference_guardrail

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

input_verdict := {"decision": "transform", "reason": "transform", "message": "", "transform": {"path": "$policy_target", "value": __transform_value}} if {
    input.intervention_point == "input"
    input.intervention_point == "input"
    input.policy_target.value != null
    is_string(input.policy_target.value)
    __transform_value := regex.replace(input.policy_target.value, "restricted_results|partner_notes|contacts|private_notes", "[REDACTED]")
}

pre_model_call_verdict := {
    "decision": "deny",
    "reason": "deny",
    "message": ""
} if {
    input.intervention_point == "pre_model_call"
    input.intervention_point == "pre_model_call"
    input.annotations.sharing_class_classifier == "restricted_results"
}
else := {
    "decision": "deny",
    "reason": "deny",
    "message": ""
} if {
    input.intervention_point == "pre_model_call"
    input.intervention_point == "pre_model_call"
    input.annotations.sharing_class_classifier == "partner_notes"
}
else := {
    "decision": "deny",
    "reason": "deny",
    "message": ""
} if {
    input.intervention_point == "pre_model_call"
    input.intervention_point == "pre_model_call"
    input.annotations.sharing_class_classifier == "contacts"
}
else := {
    "decision": "deny",
    "reason": "deny",
    "message": ""
} if {
    input.intervention_point == "pre_model_call"
    input.intervention_point == "pre_model_call"
    input.annotations.sharing_class_classifier == "private_notes"
}

post_model_call_verdict := {
    "decision": "escalate",
    "reason": "escalate",
    "message": ""
} if {
    input.intervention_point == "post_model_call"
    input.intervention_point == "post_model_call"
    input.annotations.restricted_inference_llm == "high"
}

output_verdict := {
    "decision": "warn",
    "reason": "warn",
    "message": ""
} if {
    input.intervention_point == "output"
    input.intervention_point == "output"
    input.annotations.restricted_inference_llm == "medium"
}
