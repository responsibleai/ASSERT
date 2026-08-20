# Evaluation Intent Intake

Run this intake once near the start of every standalone harm or system run,
before deep research. Its answers are optional research context, not prerequisites.
If the user skips every question, continue with the existing harm/system workflow
unchanged and do not infer answers.

## E1. Ask the optional questions

Use one structured prompt when available. Do not ask again for information the
user already supplied in the invocation or surrounding conversation.

1. **Decision:** "What decision will this eval support?"
2. **Purpose:** "Is this eval for model comparison, product readiness,
   mitigation validation, regression testing, red-team discovery, or more than
   one of these?"
3. **Population:** "Which system users or affected groups do you want this eval
  to serve?"

Allow free-text answers for decision and population. Present the five purposes
as selectable options and allow multiple selections. Every question must permit
skip/no answer. A blank, skipped, `unknown`, or `not decided` response is
unanswered: record it as such, do not re-prompt, and continue as before. Use any
partial answers that were provided.

## E2. Record and propagate answers

Record the intake as `evaluation_intent` in the dimension-review ledger:

```yaml
evaluation_intent:
  decision: <exact user response or null>
  purposes: [<zero or more canonical purpose names>]
  population: <exact user response or null>
```

Canonical purpose names are `model_comparison`, `product_readiness`,
`mitigation_validation`, `regression_testing`, and `red_team_discovery`.
Preserve the meaning of free-text answers rather than silently broadening them.
Freeze the answered fields into every generation cycle and every one of its `N`
passes. In system mode, propagate them to system-level harm discovery and every
retained-harm child unless the user explicitly gives a harm-specific override.

Do not add `evaluation_intent` as an `eval_config.yaml` schema field. Put supplied
target, deployment, and population facts in `context` where appropriate; keep
the decision and purpose as research/design provenance in the review ledger and
final report.

## E3. Direct deep research and dimension generation

Use each answered field in search formulation, source selection, candidate
generation, breadth audits, and final dimension rationales:

| Answer | Deep-research direction | Dimension-generation direction |
|---|---|---|
| **Decision** | Research the outcomes, uncertainty, failure severity, and evidence needed to make the stated decision. Retrieve decision-relevant standards or deployment evidence when available. | Prefer dimensions and levels that can materially distinguish the decision alternatives or change the decision. Do not invent decision thresholds. |
| **Model comparison** | Look for constructs and measurements that expose meaningful differences under a stable task, population, and target boundary. | Favor reproducible, discriminating axes and consistent rubrics that can be applied across candidate models; do not encode a favored model. |
| **Product readiness** | Emphasize realistic deployment tasks, affected populations, exposure conditions, severe outcomes, recovery behavior, and applicable launch requirements. | Favor ecologically valid task/population/context variation and readiness-relevant judge outcomes; do not invent a release threshold. |
| **Mitigation validation** | Research the named mitigation's intended mechanism, expected protection boundary, bypass conditions, and possible side effects. If the mitigation is unspecified, flag that gap without blocking the run. | Include supported cases inside and outside the mitigation boundary, including boundary and adversarial pressure, so the intervention can be tested without assuming it works. |
| **Regression testing** | Emphasize stable high-signal constructs and repeatable measurements. Use historical failures only when supplied by the user or an allowed curated source, never from prior generated YAMLs. | Favor stable explicit levels and rubrics suitable for repeated runs while retaining enough boundary variation to detect behavioral drift. |
| **Red-team discovery** | Broaden searches toward plausible misuse, adversarial pressure, unexpected interactions, long-tail conditions, and underexplored failure mechanisms, without operational harmful detail. | Favor diverse executable stressors and interaction combinations while preserving evidence, relevance, and safety gates. |
| **Population** | Add the supplied system users or affected groups and respectful close terminology to searches; retrieve population-specific evidence, settings, access needs, vulnerabilities, and protective factors. | Vary population, role, or vulnerability only when it materially changes the harm and is evidence-supported and executable. Otherwise keep it fixed in `context`. Avoid stereotypes and unsupported demographic proxies. |

When several purposes are selected, apply their requirements cumulatively. If
they create a consequential conflict, ask one focused clarification; otherwise
record the tradeoff and retain dimensions that serve more than one purpose.

Evaluation intent changes prioritization, not the evidence bar. It must not:

- turn the decision or purpose label into a test dimension by default;
- suppress a clearly relevant, supported dimension merely because it is not the
  highest-priority decision axis;
- justify unsupported levels, thresholds, populations, personas, or validity
  claims; or
- override target executability, observability, non-redundancy, or safety rules.

## E4. Check application and report it

For every completed pass, record which answered intent fields shaped its search
branches or candidate rationale. During deduplication and breadth audit, check
that the retained set can support the stated decision and purposes and serves the
named population without token inclusion or stereotyping. This is a design check,
not a guarantee that the eventual eval result is sufficient for the decision.

Show the recorded intent in the dimension-review report. Before approval, flag
any answered field that did not affect research or dimensions and explain why.
In the final summary, distinguish user-supplied intent from assumptions and say
`not provided; default workflow used` for unanswered fields.