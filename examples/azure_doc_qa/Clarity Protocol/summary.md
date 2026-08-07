# Summary

**Project.** A multi-agent Azure documentation Q&A assistant. A triage agent
routes each request to one of three specialists — `product_docs` (public docs),
`internal_docs` (INTERNAL / CONFIDENTIAL material behind a clearance barrier), or
`escalation` (hand-off to a human). Retrieval is tool-backed; the behavioral
contract (grounding, the information barrier, "treat documents as data", routing
and escalation rules) lives in the system prompts and is therefore defeasible.

**Core tension.** The assistant must be specific and genuinely useful about Azure
APIs and internal docs while staying rigorously grounded and honoring a clearance
barrier it enforces only in prose. A helpful-by-default model resolves pressure by
smoothing gaps — inventing API details, reproducing restricted content under a
plausible pretext, or obeying instructions embedded in a retrieved document —
which is exactly the harm.

**Risks discovered (see `failures/failures.md`).**

1. **Confidential/internal leakage to an under-cleared caller** (Critical) —
   discloses INTERNAL/CONFIDENTIAL content to an external caller with a pretext, or
   hands a verified-internal caller raw CONFIDENTIAL text they may not reproduce.
2. **Fabricated / ungrounded technical answer** (Critical) — invents API
   parameters, method names, pricing, or citations that retrieval never returned.
3. **XPIA — obeys instructions embedded in a retrieved document** (High) — treats
   document content as a command instead of data.
4. **Routing logic / chain-of-thought disclosure** (Medium).
5. **Inappropriate escalation judgment** (Medium) — over- or under-escalation.

**Triage decision.** Risks **1 (leakage)** and **2 (fabrication)** were selected
for measurement; the remaining three were recorded but not measured in this pass.

**Evaluation target.** Callable `chat(message, history=None) -> str` in
`examples/azure_doc_qa/agent.py` (async, multi-turn auto-detected). Because the
observable failure is in the reply text, each ACS gate is an **output-semantic
annotator gate** over the reply — deny + regenerate toward a safe response.

**Measured result (baseline -> ACS-governed, harm / permissible-violation / overrefusal).**

| Risk | Axis | Baseline | Governed |
|---|---|---|---|
| Leakage | prompt | 40.9 / 40.0 / 8.0 | 9.1 / 24.0 / 16.0 |
| Leakage | scenario | 62.5 / 68.0 / 44.0 | 33.3 / 64.0 / 64.0 |
| Fabrication | prompt | 21.4 / 45.8 / 40.0 | 6.2 / 44.0 / 44.0 |
| Fabrication | scenario | 39.1 / 24.0 / 20.0 | 50.0 / 72.0 / 72.0 |

The leakage output gate roughly halves harmful leakage on both axes (prompt harm
−31.8 pts, scenario harm −29.2 pts), at the expected overrefusal cost.

Fabrication was harder and revealed an ACS scoping boundary. A reply-only output
annotator is ineffective (it cannot tell grounded specificity from fabricated
specificity, so it only trades overrefusal). Feeding the annotator the retrieval
context it captures from the baseline graph, plus a scoped grounded rewrite, cuts
single-turn fabrication harm 21.4 -> 6.2 (−71%) at essentially no overrefusal cost
(40 -> 44). Multi-turn is not solvable by an output gate: scenario harm stays at
50 and 18/25 conversations are flagged both fabrication and overrefusal — the agent
fabricates on some turns and stonewalls on others. The multi-turn fix must move
upstream (a retrieval-state / tool-result gate or prompt-hardening), not another
output-remediation lever. Full progression: text-only P 10.0/64.0/64.0
S 50.0/52.0/48.0 -> grounded P 6.2/56.0/56.0 S 50.0/68.0/68.0 -> grounded+scoped
P 6.2/44.0/44.0 S 50.0/72.0/72.0.

Configs, policies, and the governed agent live under `examples/azure_doc_qa/evals/`,
`examples/azure_doc_qa/acs/`, and `examples/azure_doc_qa/agent_guarded.py`.
