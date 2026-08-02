package agent_control_specification.azure_doc_qa_internal_doc_disclosure_baseline

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
    input.tool.name == "verify_caller_identity"
    input.annotations.internal_doc_disclosure_tool_payload_classifier == "deny"
}
else := {
    "decision": "deny",
    "reason": "deny",
    "message": ""
} if {
    input.intervention_point == "pre_tool_call"
    input.intervention_point == "pre_tool_call"
    input.tool.name == "knowledge_base_retrieve"
    input.annotations.internal_doc_disclosure_tool_payload_classifier == "deny"
}
else := {
    "decision": "deny",
    "reason": "deny",
    "message": ""
} if {
    input.intervention_point == "pre_tool_call"
    input.intervention_point == "pre_tool_call"
    input.tool.name == "search_internal_docs"
    input.annotations.internal_doc_disclosure_tool_payload_classifier == "deny"
}
else := {
    "decision": "deny",
    "reason": "deny",
    "message": ""
} if {
    input.intervention_point == "pre_tool_call"
    input.intervention_point == "pre_tool_call"
    input.tool.name == "get_internal_document"
    input.annotations.internal_doc_disclosure_tool_payload_classifier == "deny"
}
else := {
    "decision": "deny",
    "reason": "deny",
    "message": ""
} if {
    input.intervention_point == "pre_tool_call"
    input.intervention_point == "pre_tool_call"
    input.tool.name == "microsoft_docs_search"
    input.annotations.internal_doc_disclosure_tool_payload_classifier == "deny"
}
else := {
    "decision": "deny",
    "reason": "deny",
    "message": ""
} if {
    input.intervention_point == "pre_tool_call"
    input.intervention_point == "pre_tool_call"
    input.tool.name == "escalate_to_human"
    input.annotations.internal_doc_disclosure_tool_payload_classifier == "deny"
}

post_tool_call_verdict := {
    "decision": "deny",
    "reason": "deny",
    "message": ""
} if {
    input.intervention_point == "post_tool_call"
    input.intervention_point == "post_tool_call"
    input.tool.name == "verify_caller_identity"
    input.annotations.internal_doc_disclosure_tool_result_classifier == "deny"
}
else := {
    "decision": "deny",
    "reason": "deny",
    "message": ""
} if {
    input.intervention_point == "post_tool_call"
    input.intervention_point == "post_tool_call"
    input.tool.name == "knowledge_base_retrieve"
    input.annotations.internal_doc_disclosure_tool_result_classifier == "deny"
}
else := {
    "decision": "deny",
    "reason": "deny",
    "message": ""
} if {
    input.intervention_point == "post_tool_call"
    input.intervention_point == "post_tool_call"
    input.tool.name == "search_internal_docs"
    input.annotations.internal_doc_disclosure_tool_result_classifier == "deny"
}
else := {
    "decision": "deny",
    "reason": "deny",
    "message": ""
} if {
    input.intervention_point == "post_tool_call"
    input.intervention_point == "post_tool_call"
    input.tool.name == "get_internal_document"
    input.annotations.internal_doc_disclosure_tool_result_classifier == "deny"
}
else := {
    "decision": "deny",
    "reason": "deny",
    "message": ""
} if {
    input.intervention_point == "post_tool_call"
    input.intervention_point == "post_tool_call"
    input.tool.name == "microsoft_docs_search"
    input.annotations.internal_doc_disclosure_tool_result_classifier == "deny"
}
else := {
    "decision": "deny",
    "reason": "deny",
    "message": ""
} if {
    input.intervention_point == "post_tool_call"
    input.intervention_point == "post_tool_call"
    input.tool.name == "escalate_to_human"
    input.annotations.internal_doc_disclosure_tool_result_classifier == "deny"
}

output_verdict := {
    "decision": "deny",
    "reason": "deny",
    "message": ""
} if {
    input.intervention_point == "output"
    input.intervention_point == "output"
    input.annotations.internal_doc_disclosure_output_classifier == "deny"
}
