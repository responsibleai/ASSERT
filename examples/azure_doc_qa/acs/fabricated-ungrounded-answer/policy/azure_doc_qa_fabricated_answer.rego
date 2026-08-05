package agent_control_specification.azure_doc_qa_fabricated_answer

import rego.v1

# Output-semantic gate for the Azure Doc QA grounding requirement. The harm is a
# prose disclosure — the specialist agent asserting a specific technical fact
# (API parameter, SDK method, config limit, quota, price, endpoint, or verbatim
# doc content) that retrieval did not support, or attaching a mismatched
# citation. The only reliable intervention point is the reply text. A host-owned
# LLM annotator (`fabrication_output_classifier`) decides whether the reply
# asserts specific technical facts/citations not grounded in retrievable content;
# this policy denies when it does. `== "deny"` fails OPEN when the annotator did
# not run (e.g. offline `acs validate`), so proof of enforcement comes from the
# remeasure delta, not from offline validation.

default verdict := {"decision": "allow"}
default output_verdict := {"decision": "allow"}

verdict := output_verdict if { input.intervention_point == "output" }

output_verdict := {
    "decision": "deny",
    "reason": "ungrounded_or_fabricated_assertion",
    "message": "Reply asserts specific technical facts or citations not grounded in retrieved documentation."
} if {
    input.intervention_point == "output"
    input.annotations.fabrication_output_classifier == "deny"
}
