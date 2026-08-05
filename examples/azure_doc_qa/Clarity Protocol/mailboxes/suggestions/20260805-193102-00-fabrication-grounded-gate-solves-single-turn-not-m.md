# Fabrication: grounded gate solves single-turn not multi-turn

**Source:** mcp
**Target:** failures/failures.md

Annotate failure-02 (Fabricated/ungrounded answer) as MEASURED with a scoping boundary. Four-way harm/permissible-violation/overrefusal: baseline P 21.4/45.8/40.0 S 39.1/24.0/20.0; text-only output gate P 10.0/64.0/64.0 S 50.0/52.0/48.0 (ineffective -- traded huge overrefusal for little/negative harm change); grounded gate that feeds the annotator the captured retrieval context P 6.2/56.0/56.0 S 50.0/68.0/68.0; grounded + scoped regeneration P 6.2/44.0/44.0 S 50.0/72.0/72.0. Single-turn: harm 21.4pct to 6.2pct (-71pct) at neutral overrefusal (40 to 44) -- decisive win. Multi-turn: unsolved by an output gate; 18 of 25 scenario conversations flagged BOTH fabrication and overrefusal (fabricate on some turns, stonewall on others). Conclusion: output-semantic remediation fixes single-turn groundedness but not multi-turn; the multi-turn fix must move upstream (retrieval-state/tool-result gate or prompt-hardening). Impl: agent_guarded.py:chat_governed_fabrication (grounded annotator + scoped regen); configs eval_config.governed_grounded and eval_config.governed_grounded_v2.yaml.

## Rationale

Failure-02's mitigation has a measured scoping boundary that should be captured: an output annotator needs the retrieved evidence to work at all, and even then only single-turn groundedness is tractable via output remediation.
