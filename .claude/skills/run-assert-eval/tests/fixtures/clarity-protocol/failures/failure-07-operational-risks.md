# Failure: Operational and security risks

## Summary

The system's broader product surface — MCP servers, hosted services, general AI integrations, LLM provider dependencies — introduces standard infrastructure risks: data exposure, cost runaway, single points of failure, prompt injection, and capability dependencies. These are well-understood risk categories with known mitigations.

## Key Risks

**LLM provider data exposure.** All protocol content is sent to LLM providers. For sensitive projects, this content may be used for training (depending on provider terms).
- *Mitigation:* Make data flow transparent. Support self-hosted LLMs.

**Hosted service data breach.** A multi-tenant hosted service stores protocols for multiple organizations. Storage isolation or access control failures expose one organization's design thinking to another.
- *Mitigation:* Per-tenant storage isolation, authentication, standard security practices for multi-tenant SaaS.

**MCP server as single point of failure.** If Layer 3 is exposed via a single MCP server, any downtime blocks all MCP-connected products from infrastructure capabilities.
- *Mitigation:* Products should degrade gracefully when infrastructure is unavailable.

**Light guide prompt injection.** In light-implementation products, the methodology is loaded as a system prompt. A malicious modified guide could inject adversarial instructions.
- *Mitigation:* Distribute the light guide through trusted channels.

**LLM cost accessibility.** A full clarity session involves many LLM calls across deep-tier models. For resource-constrained users, the cumulative cost may be prohibitive.
- *Mitigation:* The tier system allows using cheaper models for less critical tasks.

**LLM capability dependency.** Process guides assume high-capability LLMs. Smaller or open-source models may degrade.
- *Mitigation:* The tier system maps quality requirements to model capability.

## Observations

- **Severity:** Ranges from Medium (cost, capability) to Critical (data breach for hosted service)
- **Overall assessment:** These are standard infrastructure risks with well-understood mitigations.

---

## Management Plan

### Strategy

Standard infrastructure risk management — each risk is managed independently.
