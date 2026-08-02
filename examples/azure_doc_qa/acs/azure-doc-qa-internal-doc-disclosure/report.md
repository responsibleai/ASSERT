# ACS generator report: azure_doc_qa_internal_doc_disclosure_baseline

## Assumptions

### Annotators
- `internal_doc_disclosure_output_classifier` (classifier) expected labels/outputs: none declared
- `internal_doc_disclosure_tool_payload_classifier` (classifier) expected labels/outputs: none declared
- `internal_doc_disclosure_tool_result_classifier` (classifier) expected labels/outputs: none declared

### JSONPaths
- `pre_tool_call` policy_target `tool_args` at `$.tool_call.args`
  - tool name from `$.tool_call.name`
- `post_tool_call` policy_target `tool_result` at `$.tool_result`
  - tool name from `$.tool_call.name`
- `output` policy_target `assistant_output` at `$.output`

### Tools
- `escalate_to_human` from provided inventory
- `get_internal_document` from provided inventory
- `knowledge_base_retrieve` from provided inventory
- `microsoft_docs_search` from provided inventory
- `search_internal_docs` from provided inventory
- `verify_caller_identity` from provided inventory

## Not statically verified

- Classifier labels and scores match real annotator outputs.
- Policy intent fully captures the natural-language prompt.

## Warnings

- Semantic leakage categories here are best enforced with annotators because the allowed rule language cannot safely infer internal-only provenance from free-form content alone.
- The prior validation failure was caused by using inline boolean OR syntax in a rule body line; this repair preserves intent by splitting tool gating into separate per-tool deny rules.
