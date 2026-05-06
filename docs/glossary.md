# Glossary

Adaptive Eval is migrating from research-oriented names to developer-friendly names. The conceptual workflow is stable; some YAML keys still use current implementation names.

| Developer spec | Current YAML / artifact | Meaning |
|---|---|---|
| Eval spec | `spec.name`, `spec.md`, `<name>.md` | The plain-English requirements you want to evaluate. |
| About the target | `context` | Description of the agent, tools, domain, and important constraints. |
| Variations | `factors` | Axes of coverage the test generator should vary across. |
| FailureMode categories | `pipeline.taxonomy`, `taxonomy.json` | Structured taxonomy generated from the eval spec. |
| Test cases | `pipeline.seeds`, `seeds.jsonl` | Generated single-turn prompts and multi-turn scenarios. |
| Execute | `pipeline.inference`, `transcripts.jsonl` | Run test cases against the target and record conversations. |
| Target | `pipeline.inference.target` | The model, callable, or agent being evaluated. |
| OTel trace | `target.trace` | OpenTelemetry spans captured from the running agent. |
| Judge | `pipeline.judge`, `scores.jsonl` | LLM scoring against the spec and rubrics. |
| Metrics | `metrics.json` | Aggregate rates by dimension and failure_mode category. |
| Dataset / suite | `suite` | Group of shared artifacts for a set of runs. |
| Run | `run` | One execution of inference and judge against a target/config snapshot. |

## Recommended language in docs

Use:

- spec
- variations
- test cases
- execute
- judge
- artifacts
- target

Avoid using the older terms as the primary explanation:

- risk
- sub-risk
- seeds as the customer-facing noun
- inference as the customer-facing verb

When discussing YAML, use the exact current key and explain it once.
