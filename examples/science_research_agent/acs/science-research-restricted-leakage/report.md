# ACS generator report: science_research_restricted_leakage_baseline

## Assumptions

### Annotators
- `restricted_leakage_output_semantics` (llm) expected labels/outputs: none declared
- `restricted_leakage_tool_request_semantics` (llm) expected labels/outputs: none declared
- `restricted_leakage_tool_result_semantics` (llm) expected labels/outputs: none declared

### JSONPaths
- `pre_tool_call` policy_target `tool_args` at `$.tool_call.args`
  - tool name from `$.tool_call.name`
- `post_tool_call` policy_target `tool_result` at `$.tool_result`
  - tool name from `$.tool_call.name`
- `output` policy_target `assistant_output` at `$.output`

### Tools
- `file_search` from provided inventory
- `web_search` from provided inventory

## Not statically verified

- Classifier labels and scores match real annotator outputs.
- Policy intent fully captures the natural-language prompt.

## Warnings

- The requested leakage classes are semantic and cannot be reliably enforced with deterministic content checks over input.policy_target.value alone without either severe overblocking or underblocking; LLM annotators are used accordingly.
- The multi-turn leakage/reconstruction category is only partially observable under the allowed inputs because conversation history and hidden state are unavailable to rules; the annotators should infer incremental reconstruction risk only from the current mediated content.
- Exact quotation from permissible public or external-safe sources should not be flagged; annotator instructions should distinguish restricted-class document disclosure from allowed public-source quotation.
- Offline validate will not execute annotators, so these semantic rules may not fire during static validation; that is expected for this policy design.
