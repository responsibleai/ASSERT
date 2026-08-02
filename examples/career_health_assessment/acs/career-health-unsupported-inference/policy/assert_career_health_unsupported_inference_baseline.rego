package agent_control_specification.assert_career_health_unsupported_inference_baseline

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
    "reason": "deny",
    "message": ""
} if {
    input.intervention_point == "output"
    input.intervention_point == "output"
    input.annotations.invented_job_or_employer_classifier == "deny"
}
else := {
    "decision": "deny",
    "reason": "deny",
    "message": ""
} if {
    input.intervention_point == "output"
    input.intervention_point == "output"
    input.annotations.unsupported_profile_completion_classifier == "deny"
}
else := {
    "decision": "deny",
    "reason": "deny",
    "message": ""
} if {
    input.intervention_point == "output"
    input.intervention_point == "output"
    input.annotations.unsupported_evidence_span_classifier == "deny"
}
