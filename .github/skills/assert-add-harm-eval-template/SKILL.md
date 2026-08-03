---
name: assert-add-harm-eval-template
description: 'Generate ASSERT eval_config.yaml templates from either a named harm or a system. Use eval_type=harm for exhaustive harm-specific research and one evidence-backed config. Use eval_type=system to research a system, identify its relevant quality, safety, security, privacy, fairness, domain, and operational harms, then run the harm workflow for every retained harm. Produces customer-safe configs with applicability-gated dimensions and references.'
argument-hint: '<eval_type: system|harm> <system_or_harm_name> [optional description] [optional context]'
---

# ASSERT System and Harm Eval Config Builder

Build complete, runnable ASSERT `eval_config.yaml` templates from either a
specific harm or a described system. The output mirrors the shape of the shipped examples such as
[examples/azure_managed_identity/eval_config.yaml](../../../examples/azure_managed_identity/eval_config.yaml),
[examples/azure_doc_qa/eval_config.yaml](../../../examples/azure_doc_qa/eval_config.yaml),
and [examples/prompt_agents/health_assistant_external.yaml](../../../examples/prompt_agents/health_assistant_external.yaml).

The config is **spec-driven**: it describes a harm so the pipeline can generate
probes and the judge can detect violations. It must **never contain operational
harmful content** — only descriptions used for detection and refusal.

## When to use

- The user sets `eval_type` to `system` and wants to discover which quality,
  safety, security, privacy, fairness, domain, or operational harms a system
  should be evaluated for, then generate one eval config for each retained harm.
- The user names a harm (`child_safety`, `imminent_crisis_management`,
  `violence`, `sexual_content`, `fraud_and_scams`, `prompt_injection`, etc.) and
  sets `eval_type` to `harm` to generate an eval config for it.
- The user asks to scaffold, generate, or draft an `eval_config.yaml` for a
  behavior or harm.
- The user wants behavior categories, test-set dimensions, and judge dimensions
  researched and wired into a config with sensible generation knobs.
- The user wants the proposed dimensions grounded in deep online research and
  backed by explicit citations/references to recognized frameworks, research
  papers, or official publications from credible firms.
- The harm may be psychological or relational (for example, emotional dependency,
  manipulative retention, sycophancy, or relationship entanglement) and may only
  become observable as a pattern across a long conversation.

## What it produces

- **`eval_type: harm`** — a single `eval_config.yaml` with all four pipeline
  stages populated: `systematize` → `test_set` (prompt + scenario + stratify
  dimensions) → `inference` → `judge`, plus `behavior`, `context`, and
  `default_model`.
- **`eval_type: system`** — a research-backed retained/merged/rejected harm
  ledger, a sourced description for every retained harm, and one complete
  `eval_config.yaml` produced by a bounded `eval_type: harm` child run for each
  retained harm. Default child paths are
  `examples/<system_name>/<harm_name>/eval_config.yaml`.

Every generated config includes the broadest harm-relevant, evidence-supported,
non-redundant dimension set found before research saturation. It also applies
explicit distribution, validity, and provenance checks without inventing schema
fields for them. Every researched behavior category, test-set dimension,
dimension level, and judge dimension carries an inline source citation
(`# source: … [n]`), and the config ends with a consolidated `# References` list
mapping each tag to its title and URL.

## Inputs

| Input | Required | Notes |
|---|---|---|
| Eval type | Yes | Exactly `system` or `harm` (case-insensitive; normalize to lowercase). Never infer it when the request is ambiguous. |
| System or harm name | Yes | For `harm`, e.g. `child_safety` or `violence`, and it becomes `behavior.name`. For `system`, use a stable system slug for child output paths. |
| Description | No | For `harm`, the spec for `behavior.description`. For `system`, its purpose, architecture, tasks, users, data, tools/integrations, deployment, and constraints. Source or draft missing details and flag consequential assumptions. |
| Context | No | Target tasks, population, domain, runtime, deployment, and system boundaries. If omitted, use a neutral placeholder and flag it. System mode propagates the system context to every harm child run. |
| Target shape | No | Python callable/agent, hosted model + prompt/tools, or black-box endpoint. If omitted, ask or leave a flagged placeholder. |
| Model values | No | Shared or stage-specific `name`, `temperature`, `max_tokens`, `reasoning_effort`. If skipped, write placeholders (Step 5). |

## Dispatch by eval type

1. Require `eval_type` before research or file generation. Normalize it to
   lowercase and accept only `system` or `harm`. If it is missing or invalid,
   ask the user to choose; do not silently infer a branch.
2. For `eval_type: harm`, follow the existing harm procedure in Steps 1–7 below
   without changing its research, evidence, generation, or validation gates.
3. For `eval_type: system`, follow the system procedure below. It must end by
   re-entering this dispatcher once per retained harm with `eval_type: harm`.
   A harm child run must never invoke the system branch, so recursion depth is
   bounded to one fan-out level.

## System procedure (`eval_type: system`)

Read and follow the complete
[system eval workflow](./references/system-eval-workflow.md). Every stage is
mandatory. It ends by re-entering this skill in `eval_type: harm` once per
retained harm and reporting each child config's generated/validated status.

## Harm procedure (`eval_type: harm`)

### 1. Collect the harm and options

Ask the user for the harm name if not already given. Confirm whether they want to
provide a `behavior.description` and `context`, or have you source/draft them.
Identify the target shape: use `target.callable` with `target.trace` for an agent
or non-trivial Python entrypoint, `target.model` plus optional `target.tools` for
a hosted Prompt Agent, and `target.endpoint` only for a black-box API without a
Python integration. If unknown, leave a flagged target placeholder rather than
silently substituting a hosted model. Keep this short — accept "just use
defaults" for the remaining options and proceed.

### 2. Reuse a repo behavior spec before researching

The repo already ships curated, customer-safe specs for many harms. **Check these
first** and reuse rather than reinventing:

- Library presets (reference by name): [assert_ai/library/behaviors/](../../../assert_ai/library/behaviors/) — e.g. `child_safety.yaml`, `imminent_crisis_management.yaml`, `violent_content.yaml`, `sexual_content.yaml`, `prompt_injection.yaml`, `fraud_and_scams.yaml`.
- Copy-in references: [examples/behavior_specs/](../../../examples/behavior_specs/).

If a matching preset exists, prefer:

```yaml
behavior:
  preset: child_safety   # fills name + description from the library
```

or copy its `description` inline. If the harm has no repo spec (e.g. a generic
"violence" ask that maps to `violent_content`), map it to the closest spec and
tell the user, or draft a new inline description in the same
`# Title` / `## Key Terms` / `## Behavior Categories` structure as the existing
specs. Note the library preset's `suggested_judge_presets` — reuse them in Step 6.

### 3. Deep-research a harm-specific dimension model

The goal is not a generic 2–4 axis template. Discover **as many relevant,
evidence-supported, non-redundant dimensions as possible**, then stop at research
saturation rather than at an arbitrary count. Treat dimensions as an experimental
design: only materialize an axis when it is relevant, variable, observable, and
executable in the target. Pull category and dimension structure only — never
operational harmful detail.

#### 3a. Classify the harm and its observability

1. State the harm mechanism, affected population, target behavior, tasks/use
  cases, domain, interaction/runtime setting, deployment context, and observable
  outcome. Classify whether evidence appears in one response, across several
  turns, cumulatively across a trajectory, or in a downstream action. A harm can
  occupy more than one class.
2. Identify the relevant research disciplines before searching. Content and
   security harms may draw on safety taxonomies, security standards, and policy.
   Psychological or relational harms may additionally require HCI, psychology,
   psychiatry, behavioral science, child development, coercive-control,
   persuasion, parasocial-relationship, anthropomorphism, and longitudinal
   human-AI interaction literature.
3. Search the exact harm and close synonyms across these source types:
   - **Frameworks & taxonomies** — e.g. **MLCommons AILuminate**; **NIST AI RMF
     1.0** and NIST AI 600-1; **Microsoft Responsible AI** / Azure AI Content
     Safety; **OWASP Top 10 for LLM Applications**.
   - **Regulators & standards bodies** relevant to the harm (e.g. 988/WHO for
     crisis, NCMEC for child safety, FTC for fraud, EU AI Act Annex III).
   - **Peer-reviewed / preprint research** about the harm, its mechanisms,
     moderators, measurements, temporal development, or evaluation. Prefer
     peer-reviewed work; use a preprint when it is the primary source.
   - **Official technical, safety, or policy publications** from credible firms
     such as OpenAI, Anthropic, Google/DeepMind, Microsoft, and Meta.
4. Retrieve and read the primary pages or papers. Search snippets and model memory
   are leads, not evidence.

#### 3b. Build and expand a dimension ledger

For every candidate, record: evaluation role, harm/target relevance, whether it
can vary per case, observability timescale, validity contribution, intended
distribution, candidate levels, supporting sources, and disposition (`keep`,
`merge`, or `reject`). Use the areas below as discovery prompts, not as a required
dimension set or schema. Consider them only where they plausibly apply to the
named harm and target; retain an area only when it passes the evidence and
feasibility gates. When a superficially relevant area is excluded, record a short
rationale. Clearly irrelevant areas need not appear in the config or ledger.

| Discovery prompt | ASSERT representation and applicability rule |
|---|---|
| **Construct** | Always define the harm through `behavior.description`, systematized permissible/non-permissible categories, and the reserved behavior axis. Never duplicate it as a user-authored `stratify` dimension. |
| **Task / use case** | Stratify when the deployed target materially changes across QA, advice, summarization, coding, classification, or tool-mediated work. Fixed single-purpose tasks belong in `context`. |
| **Population / persona** | Stratify affected groups, user roles, vulnerabilities, or perspectives only when they change harm likelihood, manifestation, detection, or mitigation. |
| **Interaction setting** | Use `prompt` versus `scenario` for single- versus multi-turn cases. Put fixed RAG, file, tool, and agent topology in `context` and `inference.target`; stratify only settings the runtime can actually vary per case. Never label a case as tool/RAG/file-enabled when the target cannot enact it. |
| **Distribution** | Treat as experimental design and validation, not a stratification dimension. ASSERT's strength-2 covering array targets pairwise level coverage; it does not guarantee a full Cartesian product, exact balance, or matched pairs. |
| **Validity** | Treat content validity and ecological validity as design gates, not dimensions. State the inference each source supports; never claim construct, criterion, or ecological validity without evidence. |
| **Context / trajectory** | Consider context length, turn count, trajectory stage, prior assistant behavior, information position, and cumulative pattern as separate candidates when the target can express them and the harm makes them observable. |
| **Domain** | Stratify only a multi-domain target or cases that genuinely vary by domain. Otherwise put the fixed domain in `context` and use domain-specific evidence. |
| **Test spectrum** | Cover permissible/positive, non-permissible/negative, boundary/ambiguous, adversarial, and counterfactual cases across the taxonomy and test set. Add a case-type axis only when it adds variation beyond behavior categories. A covering array alone does not create exact matched counterfactual pairs. |
| **Provenance** | Record exact model names/snapshots and supported controls (`temperature`, `max_tokens`, `reasoning_effort`) in shared or stage-specific model blocks, plus `run`, `judge.n`, `max_turns`, and relevant runtime limits. This is configuration provenance, not a test dimension. |
| **Actionability / severity** | Consider evidence-backed severity or consequence levels as test-set axes. Treat actionability and severe outcome/escalation as separate binary judge candidates; judge confidence is uncertainty, not severity. |

Then expand the ledger:

1. Seed candidates from the repo spec and broad sources. For the discovery prompts
  that plausibly apply, run focused searches for relevant constructs, task/domain
  taxonomies, affected populations, runtime settings, context/trajectory,
  severity/actionability, test spectrum, or validity evidence. Do not add a
  search branch solely to satisfy the checklist.
2. Research **each candidate** with the harm name, candidate synonyms, and terms
  such as `measurement`, `moderator`, `risk factor`, `longitudinal`, `taxonomy`,
  `evaluation`, or `ecological validity`.
3. Snowball through cited constructs, measures, and adjacent factors. Continue
  until two consecutive search/snowball passes produce no new relevant,
  non-redundant dimension. Merge aliases; reject candidates with a short reason.
4. **Run a breadth audit before declaring saturation.** A result with only 2–4
  retained dimensions is a warning sign for premature convergence, not a target
  range. Revisit every plausibly applicable discovery prompt and every
  harm-specific construct exposed by the sources, including severity/imminence,
  presentation or signal type, support availability, help-seeking stance,
  trajectory stage, and response pressure where relevant. For each omitted
  candidate, record whether it was merged, lacked independent evidence, was not
  executable, was not observable, or was genuinely irrelevant. An unspecified
  target blocks tool-, RAG-, file-, and deployment-specific axes; it does not by
  itself block dimensions expressible in ordinary prompt or scenario dialogue.
5. Record the final two no-new-dimension passes in the ledger. Do not claim
  exhaustive coverage or saturation when those passes and the breadth audit were
  not completed.

#### 3c. Apply a per-dimension evidence and relevance gate

Keep a dimension only when all of the following hold:

- **Harm relevance:** the literature connects it directly to the named harm's
  mechanism, likelihood, severity, manifestation, detection, or mitigation. A
  generic safety axis is not enough.
- **Independent support:** the individual dimension is supported by **at least two
  independent authoritative sources**, or one authoritative source plus the repo
  spec. Evidence for the overall harm does not automatically support every axis.
- **Experimental usefulness:** its levels can plausibly vary in the target context
  and distinguish materially different cases or judgments.
- **Executable variation:** the configured target can actually enact the claimed
  task, setting, context, tool, file, RAG, or domain variation. Descriptive labels
  without runtime support fail this gate.
- **Observability:** the test generator can express it and the judge can observe it
  at the required timescale.
- **Non-redundancy:** it is meaningfully distinct from retained dimensions; merge
  aliases and document the mapping.
- **Validity contribution:** identify whether the candidate improves content or
  ecological validity and what inference the evidence supports. Do not use a
  generic benchmark-design paper as sole support for a harm-specific axis.

When evidence is thin, keep the candidate only in the ledger as `uncited — needs
review`; never emit it as a researched config item. Identify repo-spec evidence
by exact preset/path. Cite every source that supports each retained dimension.

Use benchmark and evaluation papers as **methodological leads**, not default
references or ready-made taxonomies. During domain research, look for sources
that expose relevant task families, realistic use cases, affected populations,
interaction/context effects, coverage gaps, distribution choices, validity
evidence, metrics, or reproducibility practices. Extract only claims that apply
to the named harm and deployment; do not import a source's domain taxonomy into
an unrelated target. Retrieve every source in the current session before citing
it. A retained dimension still needs a second independent authoritative source
or the exact repo spec that supports it.

#### 3d. Cover longitudinal and psychological harms explicitly

Do not assume harms are visible in a single answer. For psychological,
relational, or cumulative harms, research dimensions such as user vulnerability,
relationship framing, assistant initiative, boundary testing and response,
escalation stage, exclusivity, retention pressure, human-support displacement,
memory/personalization, frequency or duration of interaction, and cumulative
response pattern **only when the harm-specific literature supports them**.

If the harm emerges over time:

- make `scenario` the primary test mode and keep single-turn `prompt` cases only
  for contrast or early-stage behavior;
- include evidence-backed temporal/trajectory dimensions, with levels that span
  relevant stages rather than collapsing progression into one generic level;
- set `max_turns` from the expected onset/progression of the harm, without a fixed
  6–10-turn cap;
- make judge rubrics score the whole transcript, including accumulation,
  escalation, recovery, consistency, and assistant-initiated behavior, rather
  than only the final response.

#### 3e. Record citations

**Citation rules (strict):**

- Cite **only pages you actually retrieved this session**. Never fabricate or
  guess a URL, title, or author. If you cannot find a real source for an item,
  keep it out of the config as `uncited — needs review`; use `# source: repo
  spec: <exact preset/path>` only when that file actually supports it.
- Any of the source types above is acceptable: frameworks/taxonomies, regulator
  and standards-body guidance, peer-reviewed or preprint research papers, and
  official technical/safety/policy publications (including engineering or research
  blogs) from credible firms such as OpenAI, Anthropic, Google/DeepMind, Microsoft,
  and Meta. When sources conflict, prefer standards bodies and peer-reviewed work,
  then official firm policy/technical posts, then preprints; avoid pure marketing
  pages, SEO content, and unattributed third-party blogs.
- Keep a running **reference list** (`tag → title → URL → accessed date`). You
  embed it in the config (Step 6) and surface it in the final summary (Step 7).
- Cite every retained dimension with all sources that passed its evidence gate.
  Cite a level too when its cardinality, threshold, stage, or population comes
  from a source not already clearly attached to the parent dimension.

Extract three things, and give **each item** a citation tag:

1. **Behavior categories** — all supported permissible and non-permissible
  behaviors found before saturation. These seed `pipeline.systematize` and set
  `behavior_category_count`.
2. **Test-set dimensions** — every retained contextual, population, task,
  pressure, severity, temporal, and trajectory axis relevant to this harm. These
  become `pipeline.test_set.stratify.dimensions`.
3. **Judge dimensions** — every retained, independently scorable outcome or
  response-quality field specific to this harm (for example,
  `harm_actionability`, `refusal_quality`, or `escalation_judgment`). These become
  `pipeline.judge.dimensions`, on top of an appropriate judge preset.

### 4. Set generation knobs from the research

Tune knobs to the breadth of the harm rather than leaving defaults:

| Knob | Location | Guidance |
|---|---|---|
| `behavior_category_count` | `pipeline.systematize` | Match the supported categories found before saturation; do not impose a generic maximum. |
| `web_search` | `pipeline.systematize` | Keep `true` so systematization can expand categories with current context. |
| `prompt.sample_size` | `pipeline.test_set.prompt` | Single-turn probes. A 2–5 case smoke test checks wiring, not coverage; size coverage runs from categories, retained levels, and covering-array tuples. |
| `scenario.sample_size` | `pipeline.test_set.scenario` | Multi-turn probes (need a `tester`). Make these primary and numerous enough to span evidence-backed trajectories when the harm is cumulative. |
| `stratify.dimensions` | `pipeline.test_set.stratify` | Include every retained relevant, supported, non-redundant dimension; there is no fixed dimension count. |
| Explicit `levels` | Each `stratify.dimensions[]` | Choose each dimension's own evidence-based cardinality (minimum 2). Binary, ordinal, staged, or categorical dimensions may have different counts. |
| `stratify.level_count` | `pipeline.test_set.stratify` | Applies only to generated-mode dimensions and is shared by all of them. It may be any useful positive integer greater than 1; `3` is only the schema default. Use explicit mode when dimensions need different counts or literature-defined levels. |
| `max_turns` | `pipeline.inference` | Single-turn only → 2. For longitudinal harms, set enough turns to expose onset, escalation, boundary response, and possible recovery; do not impose a generic cap. |
| `concurrency` | `pipeline.inference` | 1 while debugging; raise within rate limits for throughput. |
| `judge.n` | `pipeline.judge` | 1 by default; 3 for majority-vote stability on borderline harms. |
| `judge.preset` | `pipeline.judge` | `safety-core` for content-safety harms; add `safety-extended` for nuanced coverage. |

Coverage and cost grow with categories, dimensions, levels, and sample size. Do
not solve cost pressure by suppressing researched dimensions. Put every retained
dimension in the single generated config and preserve the full dimension ledger.
When execution would be impractical, recommend smaller smoke-test sample sizes or
clearly named subsets the user can select later; do not emit separate core and
extended configs by default. ASSERT targets strength-2 pairwise coverage, not a
full Cartesian or exactly balanced dataset. Before claiming representation,
inspect generated factor counts and pairwise cells. Until then, call the
distribution planned rather than observed. Also inspect generated case semantics:
factor counts alone cannot prove positive, negative, boundary, adversarial, or
counterfactual coverage when case type is not an explicit axis.

### 5. Collect model values (offer to skip)

Ask whether one model config applies to every stage or whether systematization,
test generation, target, tester, and judge need distinct model names/snapshots.
Collect supported `temperature`, `max_tokens`, and `reasoning_effort` values.
For reproducible runs, pin differing stage model blocks and `run`, `judge.n`,
`max_turns`, and runtime limits in YAML. Do not force `temperature` on a reasoning
model or invent unsupported controls. If the user skips this, write a marked
placeholder and set only `default_model` so all stages fall back to it:

```yaml
default_model:
  name: azure/<your-deployment>   # TODO: set your litellm model, e.g. azure/gpt-5.4-mini
```

Never read, print, or infer values from `.env`. Use placeholder credential names
only (`AZURE_API_KEY`, `AZURE_API_BASE`, `azure_ad_token`,
`azure_ad_token_provider`). Model `name` uses litellm `provider/model` form.

### 6. Assemble and write the config

Write the file (default path `examples/<harm_name>/eval_config.yaml`, or where the
user asks). Use the skeleton below. Fill `behavior`, `context`, and every retained
researched category/dimension in this one exhaustive config. Wire a safety judge
preset plus the harm-specific judge dimensions from Step 3. Attach the Step 3
citations:

- Behavior categories live inside the `behavior.description` literal block, so cite
  them with inline text — `(source: <short title> [n])` — not a `#` comment.
- `stratify` dimensions and `judge` dimensions are real YAML structures, so cite
  them with a trailing `# sources: <short title> [n]; <short title> [m]` comment.
- Cite explicit dimension levels when their boundaries or stages rely on distinct
  evidence. Use comments only; citations are not schema fields.
- Append the consolidated `# References` block at the end of the file, mapping
  each tag `[n]` to its title, URL, and access date.

Citations live in YAML comments or literal-block text only — they never become
schema fields, so the config stays valid and customer-safe. Put fixed task,
domain, population, and RAG/tool/file/agent facts in `context` and `target`; do
not add distribution, validity, or provenance keys that the schema does not
support.

### 7. Validate

- Frontmatter/keys match [docs/config/schema.md](../../../docs/config/schema.md):
  `behavior`, `context`, `default_model`, and `pipeline` with `systematize`,
  `test_set`, `inference`, `judge`.
- `test_set` defines at least one of `prompt` or `scenario`. Scenario cases
  require a `tester`.
- All `stratify.dimensions` use one mode (all explicit `levels`, or all generated
  `description`).
- Explicit dimensions each have at least two levels, but may have different level
  counts. Generated dimensions share `stratify.level_count`; it is selected from
  the research rather than left at `3` by habit.
- Every `judge.dimensions` entry has both `description` and `rubric`.
- The selected Step 3b discovery prompts were applied proportionately: retained areas pass
  the evidence and feasibility gates, and any superficially relevant excluded
  area has a short rationale. The config does not instantiate irrelevant areas.
  Construct coverage includes both permissible and non-permissible categories.
- Every researched behavior category has an inline `(source: … [n])` note, and
  every retained `stratify`/`judge` dimension cites at least two independent
  authoritative sources (or one plus the repo spec); each `[n]` resolves to an
  entry in the `# References` block.
- The dimension ledger accounts for candidates as kept, merged, or rejected;
  discovery continued to saturation and no arbitrary dimension cap was applied.
- Every retained dimension appears in the single generated config; none were
  omitted or moved to a separate artifact merely to reduce execution cost.
- Every retained dimension has a documented, literature-backed connection to the
  named harm and is usable in the target deployment. Remove generic dimensions
  that fail this relevance test.
- Every retained interaction, task, context, or domain axis can be enacted by the
  configured target. The planned budget represents positive, negative, boundary,
  adversarial, and relevant counterfactual cases without claiming exact matched
  pairs or balance that the covering array does not guarantee.
- Validity claims name the supported inference; content/ecological validity is
  not inferred from citations alone. Model snapshots and supported controls are
  explicit enough for the requested reproducibility.
- Longitudinal harms use scenario-heavy generation, evidence-based temporal
  dimensions and turn depth, and whole-transcript judge rubrics.
- Every reference URL was actually retrieved in this session — no fabricated or
  guessed links. Repo-spec evidence names its exact preset/path; uncited
  candidates remain only in the ledger and out of the config.
- The config describes the harm for detection/refusal only — no operational
  harmful content.

Then report the reference list back to the user (tag → title → URL) so the
provenance of each dimension is visible, and suggest a dry run:

```bash
assert-ai run --config examples/<harm_name>/eval_config.yaml
```

## Skeleton

Load and fill [the eval config template](./assets/eval-config-template.yaml).
Preserve its four pipeline stages, citation comments, and references block while
replacing every placeholder from the research and target inputs.

## Safety rules

- Keep everything customer-safe and free of operational harmful content.
- Never read, print, commit, or infer secrets from `.env` or environment files.
- Reuse repo presets over hand-authored specs when they exist.
- Flag any placeholder (`context`, model `name`) the user still needs to fill.
- Cite only sources you actually retrieved this session; never fabricate or guess
  a URL, title, or author. Keep unsourced candidates only in the ledger as
  `uncited — needs review`; never emit them in the config.

## Related

- Faster interactive alternative: `assert-ai init --model <litellm-model> --describe "..."`.
- Schema reference: [docs/config/schema.md](../../../docs/config/schema.md).
- Behavior presets: [assert_ai/library/behaviors/](../../../assert_ai/library/behaviors/).
- Judge presets: [assert_ai/library/judges/](../../../assert_ai/library/judges/).
