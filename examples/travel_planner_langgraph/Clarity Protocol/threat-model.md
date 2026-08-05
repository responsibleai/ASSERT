# Threat Model — travel_planner_langgraph

Concise threat model for the LangGraph travel planner. Both top threats rest on a single
prompt-only guardrail in `itinerary_optimizer`.

| ID | Threat | Severity | Where | One-line mitigation |
|----|--------|----------|-------|---------------------|
| T1 | Fabricated itinerary details (invented flight #/price/hotel/visa presented as fact) | Critical | `itinerary_optimizer` output | Output annotator gate + regenerate-and-re-gate |
| T2 | Budget overrun (recommends plan > stated budget) | Critical | `research`/`validate_budget` args + `itinerary_optimizer` | Structural gate: inject trusted budget, deny total > budget, feed guidance back |
| T3 | Dropped safety advisory (omits visa/safety/health) | Medium | `itinerary_optimizer` output | Output completeness check |

## Single points of failure
- **`itinerary_optimizer` prompt** — the *only* thing enforcing both grounding (T1) and,
  indirectly, budget presentation (T2). One softening under multi-turn pressure defeats
  both. No independent grounding or cost check exists downstream.
- **Model-controlled `validate_budget` args** — the budget the tool checks against is
  whatever the model passes, not the user's real stated budget, so the tool can be made
  to rubber-stamp an over-budget plan.

## Top risks to measure now
failure-01 (fabrication, semantic output gate) and failure-02 (budget overrun, structural
gate) — two distinct gate shapes, both Critical.
