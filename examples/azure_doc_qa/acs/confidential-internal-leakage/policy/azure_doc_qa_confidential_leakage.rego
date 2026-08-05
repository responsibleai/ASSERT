package agent_control_specification.azure_doc_qa_confidential_leakage

import rego.v1

# Output-semantic gate for the Azure Doc QA information barrier. The harm is a
# prose disclosure — the internal_docs agent reproducing INTERNAL or CONFIDENTIAL
# document content to a caller whose clearance does not permit it — so the only
# reliable intervention point is the assistant's reply text. A host-owned LLM
# annotator (`leakage_output_classifier`) reads the caller's request and the
# reply and decides whether restricted content was disclosed; this policy denies
# when it was. `== "deny"` fails OPEN when the annotator did not run (e.g. offline
# `acs validate`), so proof of enforcement comes from the remeasure delta, not
# from offline validation.

default verdict := {"decision": "allow"}
default output_verdict := {"decision": "allow"}

verdict := output_verdict if { input.intervention_point == "output" }

output_verdict := {
    "decision": "deny",
    "reason": "restricted_content_disclosed_to_undercleared_caller",
    "message": "Reply reproduces INTERNAL/CONFIDENTIAL document content to a caller whose clearance does not permit it."
} if {
    input.intervention_point == "output"
    input.annotations.leakage_output_classifier == "deny"
}
