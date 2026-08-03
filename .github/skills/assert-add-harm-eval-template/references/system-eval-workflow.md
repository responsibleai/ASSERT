# System Eval Workflow

This workflow is mandatory when `eval_type` is `system`. Complete every stage,
then return to `SKILL.md` and run the harm procedure once for every retained
harm.

## S1. Define the system and evaluation boundary

Collect or derive the system name and description. Establish its purpose,
in-scope tasks and decisions, user and affected populations, input/output data,
knowledge sources, components and model/agent/RAG topology, tools and external
integrations, authorization boundaries, deployment environment, domains and
jurisdictions, and plausible misuse. Identify the target shape and what the
runtime can actually vary or observe. Mark unknowns and ask only when an unknown
would materially change the harm inventory; otherwise state a conservative
assumption.

## S2. Deep-research the system's candidate harms

Use internal knowledge to seed search terms and candidate harms, never as the
sole evidence for retaining one. Perform deep online research and retrieve and
read authoritative primary sources in the current session. Research in two
passes:

1. **System and domain pass** - official documentation for the system type and
   domain; applicable laws, regulators, and standards; incident reports and
   peer-reviewed research; and recognized risk frameworks such as NIST AI RMF,
   NIST AI 600-1, OWASP Top 10 for LLM Applications, MLCommons AILuminate, and
   relevant Microsoft Responsible AI guidance.
2. **Architecture and task pass** - search each actual component, data flow,
   task, integration, and affected population together with `failure`, `harm`,
   `risk`, `misuse`, `evaluation`, `benchmark`, and close synonyms. For example,
   a RAG system may expose groundedness, source attribution, hallucination,
   retrieval/privacy, prompt-injection, task-adherence, and content-safety risks;
   a booking platform may additionally expose authorization, fraud, privacy,
   discriminatory allocation, transaction-integrity, and availability risks.
   These are discovery examples, not default required harms.

Build a **system-to-harm ledger**. For every candidate record: name, mechanism,
affected party, triggering task or component, observable outcome, evidence,
repo preset match, target executability/observability, overlap with other
candidates, and disposition (`keep`, `merge`, or `reject`) with rationale.
Audit the following families proportionately to the system; retain only those
that are relevant:

- task quality and decision harms, including factuality, groundedness,
  hallucination, task adherence, completeness, calibration, and harmful
  automation or overreliance;
- content and interaction harms, including system-relevant safety categories,
  manipulation, exclusion, accessibility, and vulnerable-population effects;
- security, abuse, and cyber harms, including prompt injection, data or model
  exfiltration, insecure tool use, authorization failures, fraud, and misuse;
- privacy and data-governance harms, including collection, disclosure,
  memorization, provenance, retention, consent, and cross-boundary data flows;
- fairness, allocation, and domain harms, including discriminatory quality or
  outcomes and regulated high-impact decisions;
- reliability and operational harms, including unavailable or stale knowledge,
  unsafe recovery, transaction integrity, monitoring gaps, and cascading
  downstream actions.

Continue search and citation snowballing until two consecutive passes add no new
relevant, non-redundant harm. Do not keep a generic harm merely because it appears
in a broad framework.

## S3. Gate and finalize the harm inventory

Keep a candidate only when all of the following hold:

- credible evidence connects it to the system type, domain, task, component,
  population, or deployment;
- the system can plausibly cause, enable, or materially worsen it;
- a generated case can express it and the configured target or trace can expose
  an observable outcome;
- it is distinct from, or explicitly merged into, another retained harm; and
- evaluating it would produce an actionable system decision or mitigation.

Prefer at least two independent authoritative sources, or one authoritative
source plus direct system documentation or an exact repo behavior preset. Cite
only sources retrieved in the current session. Report retained, merged, and
rejected candidates so broad-framework omissions remain auditable.

## S4. Research and formulate each retained harm

Run a second, harm-specific research round for every retained harm. First check
[library behavior presets](../../../../assert_ai/library/behaviors/) and
[example behavior specs](../../../../examples/behavior_specs/). Reuse a matching
preset or copy-in spec rather than rewriting it. When no description exists,
retrieve harm-specific standards, regulator guidance, peer-reviewed research,
and official technical/safety publications, then draft a customer-safe
description in the repo's `# Title` / `## Key Terms` / `## Behavior Categories`
structure. Include permissible, non-permissible, boundary, and system-specific
manifestations without operational harmful detail.

For each harm, record the exact description, source tags, preset/path if any,
why it applies to this system, and the fixed system context that its child run
must inherit. A broad system-level citation does not automatically support the
harm description; each description needs harm-specific evidence.

## S5. Initialize and execute one harm template run per retained harm

Do not stop after listing harms. For every retained harm, re-enter this skill's
dispatcher as a separate child run with this explicit payload:

```yaml
eval_type: harm
harm_name: <retained_harm_slug>
behavior_description: <researched description or exact repo preset>
context: <inherited system context plus why this harm applies>
target_shape: <the system target shape from S1>
model_values: <the shared or stage-specific values supplied by the user>
output_path: examples/<system_name>/<harm_name>/eval_config.yaml
```

Initialize and execute the full harm procedure in Steps 1-7 for each payload,
including its own dimension research, references, schema validation, and dry-run
recommendation. Do not substitute one multi-harm config for these child configs,
do not omit a retained harm to reduce cost, and do not claim completion while any
child run is uninitialized or invalid. Child runs may execute in parallel only
when their output paths and research/reference ledgers are isolated.

## S6. Report the system portfolio

Return a concise portfolio summary containing the system boundary and assumptions,
the retained/merged/rejected harm ledger with source tags, each harm description,
the path and generated/validated status of every child config, unresolved evidence
or target gaps, and the consolidated reference list. Distinguish discovered harms
from configs successfully generated and validated.