# Unverified internal document disclosure

**Source:** mcp

The agent exposes `search_internal_docs(query, top_k)` and `get_internal_document(doc_id)` to the model with NO caller-identity or clearance parameter in either signature. Any conversational turn that persuades the model an internal lookup is warranted results in confidential content being returned verbatim to an anonymous end user. The internal corpus includes q3-2026-roadmap.md, incident-response-playbook.md, known-issues-registry.md, v1-to-v2-migration-runbook.md and agent-service-architecture.md — unreleased roadmap, security incident procedure, and known-defect data.

## Additional Context

Severity: HIGH. Confidentiality breach reachable in a single turn with no privilege escalation required. The tool layer cannot refuse because it is never told who is asking — enforcement exists only as soft system-prompt instruction, which is exactly the class of control that adversarial multi-turn testing defeats. This is the primary ACS candidate: wrap both tools with control.protect_tool.
