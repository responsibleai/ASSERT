# Workflow: research-eval-dimensions

Turn an **already-identified risk** into a complete, runnable ASSERT
`eval_config.yaml`, using evidence-backed dimension research, the bundled template, and the
schema.

**Entered from** `SKILL.md` Step 3 (and from `measure-clarity-failures.md` Step 3), once the
risk source is established and triage has selected which risks to measure. Everything
upstream — Clarity discovery, user-supplied risks, triage — and everything downstream —
target shape, smoke run, the pipeline run, reporting, ACS remeasure — stays in those
documents. This workflow owns exactly one thing: turning a selected risk into an
approved, cited config.

**It does not decide *what* to test for.** The risk arrives named — from Clarity, a
red-team finding, a threat model, or the user's own risk assessment. What this workflow
supplies is ***how* that risk has been evaluated**: a literature review of prior
measurement of this harm, converted into a test-set design. The distinction matters,
because a config that merely restates the topic cannot measure it. What the research
recovers is how the harm *manifests*:

- **Timescale** — psychosocial, relational, and cumulative harms typically show up
  across turns rather than in a single answer, which drives `scenario` over `prompt`
  and sets `max_turns` from the expected onset (Step 3d).
- **Viewpoint** — a hospital helpdesk is exercised by patients, nurses, and schedulers,
  not by one adversarial persona. Population and role become stratification dimensions
  when the evidence says they change the harm.
- **Conditions** — pressure, severity, context position, and trajectory stage become
  explicit `levels` where sources support them.

The config is **spec-driven**: it describes a harm so the pipeline can generate probes and
the judge can detect violations. It must **never contain operational harmful content** —
only descriptions used for detection and refusal.

## When to use

- A risk has been selected in triage (from Clarity or supplied by the user) and
  needs an eval config — the normal case.
- The user names a harm (`suicide_self_harm`, `imminent_crisis_management`,
  `violent_content`, `sexual_content`, `hate_speech_harassment`,
  `malicious_cyber_activity`, `prompt_injection`, etc.) and wants an eval config
  generated for it.
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

Do **not** use it to decide which risks a system has. That is Clarity's job, or the
user's. See [`system-eval-workflow.md`](system-eval-workflow.md), which is retained for
parity with the upstream skill and is **not** an entry point into this one.

## Preconditions

- **Live source retrieval must be available.** Step 3e requires citing only pages actually
  retrieved this session, and Step 3c gates every dimension on at least two independent
  authoritative sources (or one plus the repo spec). Without a working retrieval tool that
  gate cannot be satisfied. Say so plainly and stop at the ledger rather than emitting a
  config with invented or remembered citations.
- **`N` is required** and is never silently defaulted — see "Inputs" below.
- **The risk is already named.** If it is not, stop and return to `SKILL.md` Step 1.

## What it produces

A single `eval_config.yaml` with all four pipeline stages populated:
`systematize` → `test_set` (prompt + scenario + stratify dimensions) →
`inference` → `judge`, plus `behavior`, `context`, and `default_model`.
A regeneration uses a new date-suffixed directory and never reads prior matching
generated YAMLs.

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
| Harm name | Yes | E.g. `child_safety` or `violence`; becomes `behavior.name`. It arrives from triage already named — this workflow does not choose it. |
| Generation runs (`N`) | Yes | Positive integer specifying how many complete dimension-generation passes to run before deduplication. Ask when it is missing or invalid; do not silently default it. |
| Evaluation intent | No | Ask what decision the eval supports, its purpose(s), and the system users or affected groups it should serve. Apply answered fields to research and dimensions; skip unanswered fields without blocking or changing the default flow. |
| Dimension criteria | Interactive | Before generation, ask for edits or criteria every pass should honor, such as clustering related dimensions, reducing granularity, limiting fictional scenarios, or prioritizing particular settings or populations. Treat the answer as cumulative criteria; `none` is valid. |
| Description | No | The spec for `behavior.description`. Source or draft missing details and flag consequential assumptions. |
| Context | No | Target tasks, population, domain, runtime, deployment, and system boundaries. If omitted, use a neutral placeholder and flag it. |
| Target shape | No | Python callable/agent, hosted model + prompt/tools, or black-box endpoint. If omitted, ask or leave a flagged placeholder. |
| Model values | No | Shared or stage-specific `name`, `temperature`, `max_tokens`, `reasoning_effort`. If skipped, write placeholders (Step 7). |

## Dispatch

1. Require a named harm and `N` before research or file generation. Accept `N`
  only when it is an integer greater than zero. If it is missing or invalid, ask
  the user to correct it rather than inferring a value.
2. Follow the harm procedure in Steps 1–9 below without changing its research,
  evidence, generation, or validation gates.

## Harm procedure

### 1. Collect the harm and options

Ask the user for the harm name if not already given. Confirm whether they want to
provide a `behavior.description` and `context`, or have you source/draft them.
Validate `N` as a positive integer. Follow the optional [evaluation-intent
intake](evaluation-intent-workflow.md); skipped answers preserve the
default flow. Before any dimension research, always ask
whether the user wants a specific edit or criterion applied during generation;
offer examples such as clustering related axes, reducing granularity, reducing
reliance on fictional scenarios, or emphasizing real deployment settings. Record
the answer, including `none`, as the initial dimension criteria.
Identify the target shape: use `target.callable` with `target.trace` for an agent
or non-trivial Python entrypoint, `target.model` plus optional `target.tools` for
a hosted Prompt Agent, and `target.endpoint` only for a black-box API without a
Python integration. If unknown, leave a flagged target placeholder rather than
silently substituting a hosted model. Keep this short — accept "just use
defaults" for the remaining options and proceed.

### 2. Reuse a repo behavior spec before researching

The repo already ships curated, customer-safe specs for many harms. **Check these
first** and reuse rather than reinventing:

- Library presets (reference by name): [assert_ai/library/behaviors/](../../../../assert_ai/library/behaviors/) — e.g. `suicide_self_harm.yaml`, `imminent_crisis_management.yaml`, `violent_content.yaml`, `sexual_content.yaml`, `hate_speech_harassment.yaml`, `malicious_cyber_activity.yaml`, `prompt_injection.yaml`, `doxxing.yaml`, `harmful_medical_advice.yaml`, `relationship_entanglement.yaml`.
- Copy-in references: [examples/behavior_specs/](../../../../examples/behavior_specs/).

Run `assert-ai library list` to see everything currently bundled, and
`assert-ai library show <name>` to print one. The loader discovers presets by
globbing the directory, so the list is always authoritative — prefer it over any
enumeration written here.

If a matching preset exists, prefer:

```yaml
behavior:
  preset: violent_content   # fills name + description from the library
```

or copy its `description` inline. If the harm has no repo spec (e.g. a generic
"violence" ask that maps to `violent_content`), map it to the closest spec and
tell the user, or draft a new inline description in the same
`# Title` / `## Key Terms` / `## Behavior Categories` structure as the existing
specs. Note the library preset's `suggested_judge_presets` — reuse them in Step 6, with
one exception: **skip `safety-core` if it is listed.** 18 of the 52 behavior presets
suggest it, but it defines dimensions named exactly `policy_violation` and `overrefusal`,
so selecting it replaces both built-in rubrics rather than adding to them. Take the other
suggestions (e.g. `safety-extended`, `grounding`) as given.
For a standalone harm, finalize its stable slug and run the
[prior-generation isolation preflight](generation-isolation-workflow.md)
before harm research. A system child instead inherits its new isolated system
root and must not inspect standalone or prior-system harm generation paths.

### 3. Deep-research one harm-specific dimension model

The harm already has a name. **The research question here is how this harm has been
evaluated** — what prior work measured, on what timescale, from whose viewpoint, and
under which conditions it varied. The answer becomes the test-set design.

The goal is not a generic 2–4 axis template. Discover **as many relevant,
evidence-supported, non-redundant dimensions as possible**, then stop at research
saturation rather than at an arbitrary count. Treat dimensions as an experimental
design: only materialize an axis when it is relevant, variable, observable, and
executable in the target. Pull category and dimension structure only — never
operational harmful detail. This section is one complete generation pass. Run
all of Steps 3a–3e from a fresh per-pass ledger each time Step 4 invokes it, while
honoring the current dimension criteria and answered evaluation intent.

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
   - **Prior evaluations of this harm** — benchmarks, red-team studies,
     measurement instruments, audits, and evaluation papers that have actually
     *tested* for it. Extract their study design, not their scores: the unit of
     analysis (single response, multi-turn dialogue, trajectory), who the probes
     were written as, which factors were varied, and which were reported to
     matter. This is the primary source type for Step 3b's ledger.
   - **Official technical, safety, or policy publications** from credible firms
     such as OpenAI, Anthropic, Google/DeepMind, Microsoft, and Meta.
4. Retrieve and read the primary pages or papers. Search snippets and model memory
   are leads, not evidence.
5. For each retrieved evaluation, record in the ledger **how it operationalized the
   harm**: interaction mode and turn count, the population or role the probes were
   authored from, the conditions varied, and how a violation was scored. A design
   choice reported by two independent evaluations is a strong dimension candidate;
   an evaluation that reports a factor changed its results is stronger still.

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
| **Population / persona** | Stratify affected groups, user roles, vulnerabilities, or perspectives only when they change harm likelihood, manifestation, detection, or mitigation. Where they do, author probes from the **target's actual primary users** — a hospital helpdesk is exercised by patients, nurses, and schedulers — rather than collapsing the whole test set into one adversarial persona. Adversarial framing is a level of this axis, not a replacement for it. |
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

Benchmark and evaluation papers are the **richest source of dimensions** — they are
where prior work recorded how this harm has to be tested to be seen at all. Mine
them for relevant task families, realistic use cases, affected populations,
interaction/context effects, coverage gaps, distribution choices, validity
evidence, metrics, and reproducibility practices.

Mine them; do not copy them. Extract only claims that apply to the named harm and
deployment; do not import a source's domain taxonomy wholesale into an unrelated
target, and do not cite a generic benchmark-design paper as sole support for a
harm-specific axis. Retrieve every source in the current session before citing
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
  embed it in the config (Step 8) and surface it in the final summary (Step 9).
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

### 4. Run `N` passes and deduplicate the dimensions

Follow the [iterative dimension workflow](iterative-dimension-workflow.md)
with its [review template](../assets/dimension-review-template.md) and [validator](../validate_dimension_review.py).
Run Steps 3a–3e `N` times even if an earlier pass reached its own saturation
gate. Keep each pass's ledger and citations distinct, then semantically
deduplicate behavior categories, test-set dimensions, and judge dimensions
within their respective roles. Preserve the union of genuinely distinct,
evidence-supported dimensions; frequency across runs is not an evidence gate.
Record aliases, source runs, merged evidence, and merge/reject rationales.

### 5. Review and revise dimensions with the user

Before collecting final generation knobs or writing any YAML, present the
deduplicated dimensions and merge decisions in a compact review table. Ask the
user both whether the dimensions are relevant and whether they want any specific
edit or additional generation criterion. Silence is not approval.

Apply direct organizational edits such as renaming, reordering, or clustering
only when the evidence and meaning remain valid, then deduplicate and present the
set again. If feedback changes the research space, evidence needs, scenario
realism, inclusion rules, or exclusions — for example, reducing reliance on
fictional scenarios — perform a fresh `N`-pass cycle under the cumulative
criteria, deduplicate again, and return to this review step. Repeat until the user
explicitly approves the final set. Do not create an `eval_config.yaml` for an
unapproved set. When several risks were selected in triage, review each harm
separately by default; a batched review is allowed only when the user explicitly
requests it.

### 6. Set generation knobs from the approved research

Tune knobs to the breadth of the harm rather than leaving defaults:

| Knob | Location | Guidance |
|---|---|---|
| `behavior_category_count` | `pipeline.systematize` | Match the supported categories found before saturation; do not impose a generic maximum. |
| `web_search` | `pipeline.systematize` | Keep `true` so systematization can expand categories with current context. |
| `prompt.sample_size` | `pipeline.test_set.prompt` | **Ask the user; never pick silently.** Use the research to compute a coverage floor (categories × retained levels × pairwise tuples), then present the tradeoff: `10` = fast/noisy first look, `25` = stable rate (recommended), `50`+ = tightest signal, cost scales linearly. Use **`≥25` whenever the run will become an ACS A/B baseline** — see the sizing note below. |
| `scenario.sample_size` | `pipeline.test_set.scenario` | Multi-turn probes (need a `tester`). Ask once and apply the answer to **both** `prompt` and `scenario` unless the user says otherwise. Make these primary and numerous enough to span evidence-backed trajectories when the harm is cumulative. |
| `stratify.dimensions` | `pipeline.test_set.stratify` | Include every retained relevant, supported, non-redundant dimension; there is no fixed dimension count. |
| Explicit `levels` | Each `stratify.dimensions[]` | Choose each dimension's own evidence-based cardinality (minimum 2). Binary, ordinal, staged, or categorical dimensions may have different counts. |
| `stratify.level_count` | `pipeline.test_set.stratify` | Applies only to generated-mode dimensions and is shared by all of them. It may be any useful positive integer greater than 1; `3` is only the schema default. Use explicit mode when dimensions need different counts or literature-defined levels. |
| `max_turns` | `pipeline.inference` | Set from the harm's evidence-backed observability horizon, with a **floor of `12`** (the config template's baseline; `DEFAULT_TESTER_MAX_TURNS` is `10`). For longitudinal harms, set enough turns to expose onset, escalation, boundary response, and possible recovery; do not impose a generic cap. Only go below the floor (`4`–`6`) when the harm is genuinely single-turn *and* the user wants a cheaper run. Keep the value **identical in baseline and governed configs** — see the multi-turn note below. |
| `concurrency` | `pipeline.inference` | 1 while debugging; raise within rate limits for throughput. |
| `judge.n` | `pipeline.judge` | 1 by default; 3 for majority-vote stability on borderline harms. |
| `judge.preset` | `pipeline.judge` | `safety-extended` for nuanced coverage (additive: `harm_actionability`, `pii_leakage`). **Do not use `safety-core`** — it defines dimensions named exactly `policy_violation` and `overrefusal`, so it replaces both built-in rubrics instead of adding to them; the built-ins already provide both. When Step 2's preset lists `safety-core` under `suggested_judge_presets`, skip that entry. |
| `systematize.model` + `judge.model` | `pipeline` | **Pin both to the strong model** (e.g. `azure/gpt-5.4`) while `default_model` stays cheap (e.g. `azure/gpt-5.4-mini`) for target, test-set, and tester. See Step 7. |

> **Sizing for noise (why a first-run `10` is often too small).** Each rate is
> `violations / sample_size`, so at `sample_size: 10` **one flipped case moves the number
> 10 percentage points**. Inference is non-deterministic (agent temperature is 1.0; gpt-5
> models can't be pinned lower), so two independent runs of the *same* config drift by a
> case or two purely by chance. That noise is harmless for a quick "is it broken?" look,
> but it **wrecks an ACS before/after A/B**: a phantom ±10pp swing can masquerade as a
> governance effect, or hide one. Recommend `25`; require `≥25` for an ACS baseline.

> **`max_turns` caps the alternating tester↔target loop** for **scenario** cases only
> (single-turn `prompt` cases ignore it). Many of the strongest findings are **multi-turn
> erosion** — the agent holds firm for a few turns, then softens under pressure. A low cap
> like `2` truncates the attack before it lands and **understates the bad-event rate**. In
> an ACS A/B a mismatch between baseline and governed would also break the "only ACS
> differs" comparison, because it changes elicitation depth.

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

### 7. Collect model values (offer to skip)

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

**Pre-fill the ground-truth split as the default** rather than letting every stage inherit
`default_model`. Run the eval cheap, but systematize and judge with the strong model:

```yaml
default_model:
  name: azure/gpt-5.4-mini      # target, test-set generation, tester
pipeline:
  systematize:
    model: azure/gpt-5.4        # authors the taxonomy
  judge:
    model: azure/gpt-5.4        # renders every verdict
```

This is the convention in the repo's own examples (`benchmark`, `change_control_agent`,
`incident_triage_agent`, `phoenix_auto_trace`, `science_research_agent`). These two stages
are not ordinary stages: `systematize` authors the behavior tree and the permissible /
non-permissible split that **every** metric is computed against, and `judge` decides both
applicability and violation for every row — on a single sample, since `judge.n` defaults to
`1` and judge temperature is not pinned. A weak model here does not add noise around a
fixed target, it *moves* the target, and it inflates run-to-run drift in applicability and
in small deltas. Verify with `assert-ai results status <suite> <run> --json`, which echoes
the model actually used at `prompt_metrics.judge_model` / `scenario_metrics.judge_model`.

Never read, print, or infer values from `.env`. Use placeholder credential names
only (`AZURE_API_KEY`, `AZURE_API_BASE`, `azure_ad_token`,
`azure_ad_token_provider`). Model `name` uses litellm `provider/model` form.

### 8. Assemble and write the config

Only after Step 5 approval and the validator's successful pre-write gate, create
the file at the preflight-selected path, then run its post-write gate. Fill
`behavior`, `context`, and every
approved retained category/dimension in this one exhaustive config. Wire a
safety judge preset plus the harm-specific judge dimensions from Step 3. Attach
the Step 3 citations:

> **Never name a researched judge dimension `policy_violation` or `overrefusal`.**
> Those are `BUILT_IN_DIMENSIONS` (`assert_ai/core/judge.py`) and are always judged unless
> explicitly disabled. Config dimensions are merged over the built-ins **by name** into the
> same dict, so reusing a built-in name **silently replaces its rubric** with the
> hand-written one — no warning, no error. This does *not* move the headline pair:
> `not_permissible_policy_violation_rate` and `permissible_policy_violation_rate` are
> recomputed in `assert_ai/results.py` from the judge's per-behavior `node_judgments`, not
> from either dimension's verdict. It does change the dimension verdict persisted in the run
> JSON and `DEFAULT_COMPARE_METRIC` (`assert_ai/cli.py`), which is still `policy_violation`
> — so a shadowed rubric makes stored results and default comparisons mean something other
> than what the engine documents. Author researched dimensions under genuinely new names only
> (e.g. `harm_actionability`, `severe_harm_escalation`, `longitudinal_harm_pattern`). The
> validator's pre-write gate enforces this.

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

### 9. Validate

- Frontmatter/keys match [docs/config/schema.md](../../../../docs/config/schema.md):
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
- **No `judge.dimensions` entry reuses a built-in name** (`policy_violation`,
  `overrefusal`), and `judge.preset` is not `safety-core` (which defines both of those
  names). The post-write gate rejects both forms; see the Step 8 note.
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
- The initial generation cycle contains exactly `N` complete per-pass ledgers,
  followed by a role-aware semantic deduplication map. Any research-changing
  user criterion triggered a fresh `N`-pass cycle under the cumulative criteria.
- Path-only preflight found no prior generation or the user approved a new dated
  directory; no prior matching generated YAML was read or reused. The selected
  config path was absent before the pre-write gate and was never overwritten.
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
provenance of each dimension is visible, and hand control back to `SKILL.md`
Step 4 (target shape) and Step 5 (smoke run, then the full run):

```bash
assert-ai run --config <selected_output_path>/eval_config.yaml
```

Do not start a full run from here. `SKILL.md` Step 5a offers a 3-case smoke run
first, which catches plumbing errors before a full suite is paid for.

## Skeleton

Load and fill [the eval config template](../assets/eval-config-template.yaml).
Preserve its four pipeline stages, citation comments, and references block while
replacing every placeholder from the research and target inputs.

## Safety rules

- Keep everything customer-safe and free of operational harmful content.
- Never read, print, commit, or infer secrets from `.env` or environment files.
- Reuse curated repo presets, but never inspect or depend on prior matching
  generated eval YAMLs.
- Flag any placeholder (`context`, model `name`) the user still needs to fill.
- Cite only sources you actually retrieved this session; never fabricate or guess
  a URL, title, or author. Keep unsourced candidates only in the ledger as
  `uncited — needs review`; never emit them in the config.

## Related

- Caller: [`../SKILL.md`](../SKILL.md) Step 3, and
  [`measure-clarity-failures.md`](measure-clarity-failures.md) Step 3.
- Sub-workflows: [iterative dimension workflow](iterative-dimension-workflow.md),
  [generation isolation](generation-isolation-workflow.md),
  [evaluation intent](evaluation-intent-workflow.md).
- Not an entry point: [system eval workflow](system-eval-workflow.md) — retained for
  parity with the upstream skill; risk identification belongs to Clarity or the user.
- Schema reference: [docs/config/schema.md](../../../../docs/config/schema.md).
- Behavior presets: [assert_ai/library/behaviors/](../../../../assert_ai/library/behaviors/).
- Judge presets: [assert_ai/library/judges/](../../../../assert_ai/library/judges/).
- `assert-ai init --model <litellm-model> --describe "..."` is the faster interactive
  scaffold. It skips every evidence and approval gate in this workflow, so use it for a
  throwaway config, not for a measurement you intend to report or govern against.
