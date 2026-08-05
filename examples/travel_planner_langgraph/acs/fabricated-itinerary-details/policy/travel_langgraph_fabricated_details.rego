package agent_control_specification.travel_langgraph_fabricated_details

import rego.v1

# Output-semantic gate for the travel planner's grounding requirement. The harm is
# a prose fabrication — itinerary_optimizer asserting a specific itinerary detail
# (flight number/airline/time/price, hotel name/rate, visa term, safety level,
# total cost, confirmation reference) that the mock tools did NOT return for the
# trip actually requested, or re-presenting the destination-mismatched mock data
# (always LAX/SFO -> dest at Tokyo-tier prices) as if it were for this trip. The
# only reliable intervention point is the reply text, judged against the tool
# observations captured from the baseline graph run. A host-owned LLM annotator
# (`fabrication_output_classifier`) decides whether the reply asserts specifics
# not grounded in those tool results; this policy denies when it does.
# `== "deny"` fails OPEN when the annotator did not run (e.g. offline
# `acs validate`), so proof of enforcement comes from the remeasure delta, not
# from offline validation.

default verdict := {"decision": "allow"}
default output_verdict := {"decision": "allow"}

verdict := output_verdict if { input.intervention_point == "output" }

output_verdict := {
    "decision": "deny",
    "reason": "fabricated_itinerary_detail",
    "message": "Reply asserts itinerary specifics not grounded in tool results for the requested trip."
} if {
    input.intervention_point == "output"
    input.annotations.fabrication_output_classifier == "deny"
}
