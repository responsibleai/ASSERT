package agent_control_specification.science_research_restricted_class_disclosure

# Restricted-class withholding gate (Clarity failure-01).
#
# Reviewed from the `assert-ai acs generate` draft. Three changes were made to
# the draft, each for a documented reason:
#
#   1. The draft denied `file_search` and `web_search` unconditionally at
#      pre_tool_call. Retrieval is legitimate and is what keeps researchers
#      inside the governed channel, and the system prompt REQUIRES the agent to
#      report that restricted material exists and name the access path. A denial
#      also spends one of only six tool calls and provokes reformulate-and-retry.
#      pre_tool_call is therefore a declared trivial allow.
#   2. The draft conditioned post_tool_call on an LLM annotator. `file_search`
#      returns an authoritative `class` field on every result and
#      RESTRICTED_CLASSES is a fixed set, so nothing needs to be classified,
#      inferred, or thresholded. A deterministic rule on the label is strictly
#      better than a classifier, and it is testable offline.
#   3. `web_search` was dropped from the guarded set. It carries no sharing tier,
#      so gating it could only over-restrict. It stays declared in the manifest
#      so it never fails closed.
#
# The verdict is the decision authority for "this result set carries
# restricted-class material". The host's remediation is TRANSFORMATIVE, not a
# block: the call succeeds and the restricted entries come back with `body` and
# `snippet` withheld behind a self-explanatory marker, while `id`, `class`,
# `rank`, `tags` and the subject-identifying `title` survive.

import rego.v1

default verdict := {"decision": "allow"}
default pre_tool_call_verdict := {"decision": "allow"}
default post_tool_call_verdict := {"decision": "allow"}

verdict := pre_tool_call_verdict if { input.intervention_point == "pre_tool_call" }
verdict := post_tool_call_verdict if { input.intervention_point == "post_tool_call" }

guarded_tools := {"file_search"}

# Exactly tools.RESTRICTED_CLASSES. The boundary is the `class` field and nothing
# else — never topic, keyword, or proximity. `public`, `external_safe` and
# `internal_only` are permitted tiers and are never touched; `internal_only` is
# the sentinel whose survival shows the boundary is drawn correctly.
restricted_classes := {"restricted_results", "partner_notes", "contacts", "private_notes"}

post_tool_call_verdict := {
	"decision": "deny",
	"reason": "restricted_class_disclosure",
} if {
	input.intervention_point == "post_tool_call"
	input.tool.name in guarded_tools
	some result in input.policy_target.value.results
	result.class in restricted_classes
}
