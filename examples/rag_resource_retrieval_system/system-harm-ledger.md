# RAG Resource Retrieval System Eval Portfolio

Accessed and generated: 2026-08-03

## System boundary and assumptions

- **Purpose:** accept a user query, retrieve relevant resources, and formulate a resource-backed response.
- **In-scope flow:** query understanding -> corpus/index retrieval -> context assembly -> response synthesis.
- **Users and affected parties:** end users asking questions; people and organizations represented in, or controlling access to, corpus resources.
- **Data:** user queries, corpus text, resource metadata, retrieval rankings, retrieved context, and generated responses.
- **Unknowns:** corpus domain, jurisdictions, resource writers, identity provider, authorization and tenancy model, ingestion controls, retrieval algorithm, model snapshot, deployment environment, and downstream consumers.
- **Target assumption:** a Python callable with OpenTelemetry traces that expose retrieval identifiers, ranks, metadata, context, and generation. Every child config contains `your_package.rag_service:chat` and `azure/<your-deployment>` as explicit TODOs.
- **Fixture assumption:** evaluation can use a disposable corpus to vary relevance, freshness, access metadata, benign/adversarial content, and provenance. Corpus-poisoning tests must never seed production data.
- **Observability:** harms may appear in one response, across a short clarification/recovery dialogue, or in retrieval events preceding the answer. No external action tools are assumed.

These assumptions are consequential. The YAML files are schema-valid templates,
but they are not executable against a real service until the callable, model, corpus
fixtures, and actual authorization rules replace the TODOs.

## System-to-harm ledger

| Candidate | Mechanism and affected party | Trigger/component and observable outcome | Evidence | Preset/spec | Disposition |
|---|---|---|---|---|---|
| Poor retrieval failures | Wrong, missing, duplicate, stale, or wrong-scope resources deprive users of usable evidence | Retriever/index; trace shows resource recall, relevance, diversity, freshness, or scope failure | [R1], [R2], [R16] | `examples/behavior_specs/poor_retrieval_failures.md` | **Keep** |
| Grounding and attribution errors | Generator fabricates, distorts, substitutes, or misattributes claims despite usable retrieval | Context assembly/generation; response claims conflict with retrieved evidence or citations | [R1], [R3], [R4], [R5], [R6] | `assert_ai/library/behaviors/grounding_attribution_errors.yaml` | **Keep** |
| Response completeness failures | Supported response silently omits requested parts or source-required details | Generation; response coverage is below the answerable/requested set | [R1], [R3], [R4] | `examples/behavior_specs/response_completeness_failures.md` | **Keep** |
| Prompt injection | User or retrieved instructions override the intended task and trust boundary | Query or retrieved text; task deviation, manipulation, or disclosure appears in response/trajectory | [R7], [R8] | `assert_ai/library/behaviors/prompt_injection.yaml` | **Keep** |
| Sensitive information disclosure | Retrieval crosses privacy, user, group, tenant, or confidentiality boundaries | Security filtering/retrieval/generation; unauthorized or excessive content appears in trace or response | [R9], [R10], [R11], [R12] | `assert_ai/library/behaviors/personal_data_privacy.yaml` (partial match) | **Keep** |
| Corpus poisoning | Manipulated corpus or embedding/index content steers retrieval or generation | Ingestion/index/retrieval; planted content is retrieved or changes targeted output | [R10], [R13], [R14], [R15], [R18] | No exact repo preset | **Keep** |
| Hallucination and misinformation | Unsupported output claim | Same observable mechanism as grounding failure for this resource-backed system | [R6], [R9] | `misinformation_and_disinformation` is broader than this deployment | **Merge** into grounding/attribution |
| Source-attribution failure | Claim mapped to the wrong resource | Same post-retrieval mechanism and evidence contract as grounding | [R1], [R3] | Grounding preset | **Merge** into grounding/attribution |
| Underused context | Correct retrieved evidence ignored | Same context-utilization mechanism as material context omission | [R3], [R4] | `examples/behavior_specs/underused_context_failures.md` | **Merge** into grounding/attribution |
| Stale knowledge | Superseded resource selected or used | Generic system has no established mutable agent state; freshness is observable at retrieval and synthesis | [R2], [R10] | `examples/behavior_specs/stale_state_failures.md` | **Merge** into retrieval freshness and grounding authority axes |
| Authorization failure | Restricted resources cross a permission boundary | Same disclosure outcome, including non-personal confidential resources | [R10], [R11], [R12] | No exact broad preset | **Merge** into sensitive disclosure |
| Indirect document attack | Retrieved resource controls generation | RAG-specific entry point of prompt injection | [R7], [R8] | Prompt-injection preset | **Merge** into prompt injection origin axis |
| Knowledge conflict | Retrieved sources or model prior disagree | A moderator of grounding and poisoning rather than a separate bad outcome | [R5], [R17] | Grounding preset partially supports | **Merge** into source-agreement and clean-evidence axes |
| Fairness or discriminatory retrieval quality | Unequal access or representation could harm affected groups | No domain, population, recommendation/allocation role, or fairness criterion was supplied | [R19], [R20] | Stereotyping preset is not a retrieval-quality spec | **Reject:** applicability and actionable metric are unresolved |
| Harmful-content generation | Retrieved or generated content may violate a domain policy | Corpus domain and content policy are unknown | [R20] | Several content presets exist | **Reject:** broad-framework risk without system-specific evidence |
| Unbounded consumption and availability | High-volume or expensive queries exhaust resources | Requires load, quota, cost, and latency instrumentation rather than conversational test cases | [R21] | None | **Reject:** not observable with the configured target contract |
| Excessive agency | Model invokes functions beyond intended authority | System description establishes retrieval only, not action tools | [R20] | Tool specs exist but do not apply | **Reject:** component absent |
| Improper output handling | Downstream interpreter executes unsafe model output | No downstream executable sink was described | [R20] | None | **Reject:** component absent |
| Retention, consent, and cross-boundary governance | Query/resource data retained or transferred improperly | Requires storage, logging, deletion, and jurisdiction evidence outside response traces | [R9], [R12] | Privacy preset partially supports | **Reject from conversational configs:** assess separately when architecture is known |
| Monitoring gaps | Failures go undetected operationally | Requires production telemetry/SLO audit, not generated dialogue | [R20] | None | **Reject from this portfolio** |

## Retained harm descriptions and child status

| Harm | Sourced description | Child config | Status |
|---|---|---|---|
| `poor_retrieval_failures` | Missing, irrelevant, redundant, stale, or wrong-scope retrieval upstream of generation [R1], [R2] | `poor_retrieval_failures/eval_config.yaml` | Generated; ASSERT schema validated |
| `grounding_attribution_errors` | Unsupported, distorted, selectively used, or misattributed synthesis after usable retrieval [R1], [R3], [R4], [R5] | `grounding_attribution_errors/eval_config.yaml` | Generated; ASSERT schema validated |
| `response_completeness_failures` | Materially partial answers that omit requested or source-required content [R1], [R3] | `response_completeness_failures/eval_config.yaml` | Generated; ASSERT schema validated |
| `prompt_injection` | Direct or retrieved instructions alter behavior outside the intended task and instruction hierarchy [R7], [R8] | `prompt_injection/eval_config.yaml` | Generated; ASSERT schema validated |
| `sensitive_information_disclosure` | Unauthorized or excessive exposure of personal, confidential, secret, or cross-context resources [R9], [R10], [R11], [R12] | `sensitive_information_disclosure/eval_config.yaml` | Generated; ASSERT schema validated |
| `corpus_poisoning` | Manipulated corpus or index content steers retrieval, corrupts knowledge, or changes targeted generation [R10], [R13], [R14], [R15] | `corpus_poisoning/eval_config.yaml` | Generated; ASSERT schema validated |

## Dimension ledgers

For every **keep** below, variation is case-level, observability is in retrieval
trace and/or response, and the intended distribution is planned strength-2
pairwise coverage across explicit levels. This plans interaction coverage; it
does not claim a full Cartesian product, exact balance, matched counterfactuals,
or observed representation. Content-validity statements are limited to the
inference supported by the cited RAG source. Ecological validity remains
conditional on replacing generic fixtures with the deployment's real corpus,
users, access rules, and query distribution.

### Poor retrieval failures

| Candidate | Role / timescale | Levels or planned variation | Evidence and validity contribution | Disposition |
|---|---|---|---|---|
| Query structure | Task; retrieval event | focused, compound, contextual/vague | [R2], repo spec; covers documented query-understanding demands | **Keep** |
| Terminology alignment | Query/retrieval; retrieval event | exact, paraphrased, domain/language variation | [R2], repo spec; tests semantic rather than lexical matching | **Keep** |
| Answer evidence pattern | Corpus; retrieval event | single sufficient, complementary, no answer | [R1], [R2], repo spec; separates recall from transparent abstention | **Keep** |
| Corpus noise | Context; retrieval event | low noise, topical distractors, duplicate crowding | [R1], [R2], repo spec; tests precision/diversity | **Keep** |
| Freshness/version | Corpus metadata; retrieval event | current, current+superseded, stale-only gap | [R2], repo spec; tests temporal applicability | **Keep** |
| Scope proximity | Corpus metadata; retrieval event | exact, adjacent distractor, mixed | [R2], repo spec; tests product/region/period/audience scope | **Keep** |
| Retrieval algorithm/top-k/chunk size | Implementation | unspecified | Could matter [R1], [R2], but runtime values are unknown and cannot be enacted honestly | **Reject: not executable** |
| Domain | Context | unspecified | Fixed domain is unknown; a generated label would not create a real corpus | **Reject: not executable** |
| Retrieval quality score | Construct | n/a | Duplicates the reserved behavior and judge outcomes | **Reject: redundant** |

### Grounding and attribution errors

| Candidate | Role / timescale | Levels or planned variation | Evidence and validity contribution | Disposition |
|---|---|---|---|---|
| Evidence support state | Context; response | full, partial, none | [R1], [R3]; tests faithful answer/qualification boundary | **Keep** |
| Source agreement | Context; response | consistent, source conflict, prior/external conflict | [R5], repo preset; tests source-aware resolution | **Keep** |
| Synthesis demand | Task; response | extraction, integration, comparison/aggregation | [R3], repo preset; distinguishes use operations | **Keep** |
| Relevant evidence position | Context; response | beginning, middle, end | [R4], [R3]; tests documented long-context position effects | **Keep** |
| Authority/freshness | Metadata; response | current authoritative, mixed, ambiguous | [R2], repo preset; tests evidence weighting/qualification | **Keep** |
| Attribution demand | Output; response | one source, corroboration, distinct claim-source mapping | [R2], repo preset; tests provenance precision | **Keep** |
| Certainty pressure | Interaction; response/trajectory | neutral, false premise, demand certainty | [R6], repo preset; tests calibrated evidence use | **Keep** |
| Hallucination | Judge outcome | n/a | Same unsupported-claim construct | **Merge** into behavior and judge |
| Underused context | Judge outcome | n/a | Same material-context-omission construct | **Merge** into judge |
| Error plausibility | Context; response | subtle to blatant | [R17] found an effect, but no second independent source or exact repo support was retrieved | **Uncited - needs review; not emitted** |
| Modality | Context | text/image/audio | Only text resources are established | **Reject: not executable** |

### Response completeness failures

| Candidate | Role / timescale | Levels or planned variation | Evidence and validity contribution | Disposition |
|---|---|---|---|---|
| Request composition | Task; response | single, independent compound, dependent compound | [R1], repo spec; tests explicit coverage obligations | **Keep** |
| Response form | Task; response | summary, attributes, comparison, procedure | [R1], [R3], repo spec; supplies form-specific completeness criteria | **Keep** |
| Answerability distribution | Corpus; response | all, some, none | [R1], repo spec; tests transparent partial answers | **Keep** |
| Evidence distribution | Context; response | one resource, complementary, overlapping+unique | [R3], [R2]; tests multi-resource recall in synthesis | **Keep** |
| Evidence position | Context; response | early, middle, late, distributed | [R4], [R3]; tests omission caused by context position | **Keep** |
| Coverage obligation | Output; response | explicit, prerequisite/step, limitation/exception | [R1], repo spec; separates kinds of material omissions | **Keep** |
| Response length | Output | short/long | Length is not completeness and can reward verbosity | **Reject: weak construct validity** |
| User persona | Population | unspecified | No evidence connected a generic persona to completeness in this deployment | **Reject: harm relevance** |
| Domain | Context | unspecified | Cannot enact without a real multi-domain corpus | **Reject: not executable** |

### Prompt injection

| Candidate | Role / timescale | Levels or planned variation | Evidence and validity contribution | Disposition |
|---|---|---|---|---|
| Injection origin | Attack surface; response/trajectory | direct, indirect resource, combined | [R7], [R8]; covers both documented entry points | **Keep** |
| User relationship to attack | Persona/intent; response | benign unaware, explicit attacker, indirect trigger | [R7], [R8]; separates indirect victim from direct attacker | **Keep** |
| Attack presentation | Signal; response | plain, role/mockup, encoded/obfuscated | [R7], [R8]; covers documented text attack classes | **Keep** |
| Attack objective | Severity/outcome; response | manipulation, disclosure, disruption | [R7], [R8]; distinguishes observable impacts | **Keep** |
| Resource trust framing | Context; response | external, authority claim, mixed content | [R7], [R8]; tests data/instruction separation under plausible framing | **Keep** |
| Conflict clarity | Boundary; response | explicit conflict, plausible command, benign control | [R7], [R8]; adds boundary and overblocking controls | **Keep** |
| Multimodal hiding | Signal | image/audio | No multimodal target was supplied | **Reject: not executable** |
| Unauthorized action execution | Downstream action | tool/API effects | No action tools were supplied | **Reject: component absent** |
| Payload splitting across resources | Context | split fragments | Retrieved only in [R7] without independent support or exact repo detail | **Uncited - needs review; not emitted** |
| Attack severity | Distribution | n/a | Materialized through objective and judge outcomes | **Merge** |

### Sensitive information disclosure

| Candidate | Role / timescale | Levels or planned variation | Evidence and validity contribution | Disposition |
|---|---|---|---|---|
| Resource sensitivity | Harm/severity; retrieval/response | public, personal, confidential, secret | [R9], [R10], repo preset; distinguishes protected content classes | **Keep** |
| Requester authorization | Population/boundary; retrieval/response | authorized, unverified, unauthorized, wrong context | [R10], [R11]; tests permission-aware retrieval | **Keep** |
| Request relationship to data | Task/population; response | own/authorized, collaborative, unrelated third party | [R9], repo preset; tests legitimate purpose | **Keep** |
| Disclosure pathway | Interaction; response | direct, incidental, inferred, boundary probe | [R9], [R10], repo preset; covers direct and emergent disclosure | **Keep** |
| Corpus partition state | Architecture; retrieval | public-only, mixed classes, multi-context | [R10], [R11]; tests isolation under shared infrastructure | **Keep** |
| Minimum necessary output | Validity/mitigation; response | none needed, redacted sufficient, limited fields required | [R12], repo preset; tests minimization rather than all-or-nothing refusal | **Keep** |
| Retention duration | Governance; downstream | unspecified | Not visible in query/response or current traces | **Reject from config: not observable** |
| Jurisdiction | Domain | unspecified | No deployment jurisdiction was supplied | **Reject: applicability unknown** |
| Consent state | Governance | unspecified | Requires real collection purpose and consent records | **Reject: not executable** |
| Prompt-injection pressure | Attack | n/a | Disclosure impact already judged in prompt-injection child | **Merge** to avoid duplicate construct |

### Corpus poisoning

| Candidate | Role / timescale | Levels or planned variation | Evidence and validity contribution | Disposition |
|---|---|---|---|---|
| Source provenance | Ingestion/context; retrieval | verified, unverified, apparently trusted compromised | [R13], [R10]; tests integrity controls | **Keep** |
| Manipulation surface | Mechanism; retrieval/response | factual corruption, relevance steering, embedded instruction | [R10], [R13], [R14]; separates poisoning mechanisms | **Keep** |
| Activation scope | Trajectory/targeting; response | clean control, broad topic, targeted semantic | [R14], [R15]; tests hidden targeted behavior | **Keep** |
| Contamination pattern | Corpus; retrieval | isolated, repeated, planted-clean conflict | [R10], [R14], [R18]; tests stealth, crowding, and conflict | **Keep** |
| Clean evidence state | Context; response | authoritative, ambiguous, absent | [R5], [R14]; tests corroboration and uncertainty | **Keep** |
| Observable impact | Severity/outcome; response | false/bias, suppression, task deviation | [R13], [R15]; distinguishes integrity consequences | **Keep** |
| Exact planted-document count | Distribution | implementation-specific thresholds | Research demonstrates counts matter [R14], [R18], but portable thresholds depend on corpus/retriever and can encourage operational attack tuning | **Reject from config** |
| Embedding algorithm | Implementation | unspecified | System implementation is unknown | **Reject: not executable** |
| Ingestion lifecycle stage | Architecture | pre-index/post-index | Current target contract evaluates retrieval and response, not ingestion jobs | **Reject: not observable** |
| Indirect prompt injection | Mechanism | n/a | Retained as embedded-instruction level; full attack taxonomy is in prompt-injection child | **Merge** |

## Breadth audit and saturation record

- **Breadth audit:** construct, task, population, interaction, trajectory,
  domain, test spectrum, actionability/severity, distribution, validity, and
  provenance prompts were reviewed for every retained harm. Domain, deployment,
  model, tool, multimodal, load, jurisdiction, and ingestion-job axes were not
  emitted when the supplied system could not enact or expose them.
- **No-new pass 1:** RAGChecker [R16] and ClashEval [R17] were retrieved after
  the focused harm research. RAGChecker mapped to existing retrieval,
  context-use, grounding, and completeness constructs. ClashEval suggested
  error plausibility, but it failed the independent-evidence gate and remains
  `uncited - needs review`. No new retained dimension.
- **No-new pass 2:** the broad RAG survey [R20] and a practical poisoning study
  [R18] were retrieved. The survey mapped to the existing retrieval/generation/
  augmentation split; the poisoning study reinforced the isolated-planted-
  resource level. No new relevant, non-redundant retained dimension.

## Validation interpretation

- All six YAML files load through `assert_ai.config.load_config`.
- Every config contains all four pipeline stages, explicit dimensions with at
  least two levels, prompt and scenario generation, a tester, a callable+trace
  target, judge rubrics, inline source tags, and a references block.
- `sample_size` values are coverage budgets, not evidence that the generated
  cases are balanced or semantically cover every positive, negative, boundary,
  adversarial, and counterfactual category. Inspect `stratification.json`,
  factor counts, pairwise cells, and generated case semantics after generation.
- Content validity is supported only for the generic text-RAG mechanisms named
  above. Ecological validity is not established until deployment-specific
  fixtures and query populations replace the placeholders.

## Consolidated references

- **[R1]** Microsoft Foundry, *Retrieval-Augmented Generation (RAG) Evaluators for Generative AI*: https://learn.microsoft.com/en-us/azure/ai-foundry/concepts/evaluation-evaluators/rag-evaluators
- **[R2]** Microsoft Learn, *RAG and Generative AI - Azure AI Search*: https://learn.microsoft.com/en-us/azure/search/retrieval-augmented-generation-overview
- **[R3]** Es et al., *RAGAs: Automated Evaluation of Retrieval Augmented Generation* (EACL 2024): https://aclanthology.org/2024.eacl-demo.16/
- **[R4]** Liu et al., *Lost in the Middle: How Language Models Use Long Contexts*: https://arxiv.org/abs/2307.03172
- **[R5]** Wang et al., *Astute RAG: Overcoming Imperfect Retrieval Augmentation and Knowledge Conflicts*: https://arxiv.org/abs/2410.07176
- **[R6]** OWASP, *LLM09:2025 Misinformation*: https://genai.owasp.org/llmrisk/llm092025-misinformation/
- **[R7]** OWASP, *LLM01:2025 Prompt Injection*: https://genai.owasp.org/llmrisk/llm01-prompt-injection/
- **[R8]** Microsoft Learn, *Prompt Shields in Azure AI Content Safety*: https://learn.microsoft.com/en-us/azure/ai-services/content-safety/concepts/jailbreak-detection
- **[R9]** OWASP, *LLM02:2025 Sensitive Information Disclosure*: https://genai.owasp.org/llmrisk/llm022025-sensitive-information-disclosure/
- **[R10]** OWASP, *LLM08:2025 Vector and Embedding Weaknesses*: https://genai.owasp.org/llmrisk/llm082025-vector-and-embedding-weaknesses/
- **[R11]** Microsoft Learn, *Security Filter Pattern - Azure AI Search*: https://learn.microsoft.com/en-us/azure/search/search-security-trimming-for-azure-search
- **[R12]** NIST, *Privacy Framework*: https://www.nist.gov/privacy-framework
- **[R13]** OWASP, *LLM04:2025 Data and Model Poisoning*: https://genai.owasp.org/llmrisk/llm042025-data-and-model-poisoning/
- **[R14]** Zou et al., *PoisonedRAG: Knowledge Corruption Attacks to Retrieval-Augmented Generation*: https://arxiv.org/abs/2402.07867
- **[R15]** Xue et al., *BadRAG: Identifying Vulnerabilities in Retrieval Augmented Generation*: https://arxiv.org/abs/2406.00083
- **[R16]** Ru et al., *RAGChecker: A Fine-grained Framework for Diagnosing Retrieval-Augmented Generation*: https://arxiv.org/abs/2408.08067
- **[R17]** Wu et al., *ClashEval: Quantifying the tug-of-war between an LLM's internal prior and external evidence*: https://arxiv.org/abs/2404.10198
- **[R18]** Zhang et al., *Practical Poisoning Attacks against Retrieval-Augmented Generation*: https://arxiv.org/abs/2504.03957
- **[R19]** Gausen, Mitra, and Lindley, *A Framework for Exploring the Consequences of AI-Mediated Enterprise Knowledge Access and Identifying Risks to Workers*: https://www.microsoft.com/en-us/research/publication/a-framework-for-exploring-the-consequences-of-ai-mediated-enterprise-knowledge-access-and-identifying-risks-to-workers/
- **[R20]** Gao et al., *Retrieval-Augmented Generation for Large Language Models: A Survey*: https://arxiv.org/abs/2312.10997
- **[R21]** OWASP, *LLM10:2025 Unbounded Consumption*: https://genai.owasp.org/llmrisk/llm102025-unbounded-consumption/
- **Repo sources:** `examples/behavior_specs/poor_retrieval_failures.md`, `examples/behavior_specs/response_completeness_failures.md`, `examples/behavior_specs/underused_context_failures.md`, `examples/behavior_specs/stale_state_failures.md`, `assert_ai/library/behaviors/grounding_attribution_errors.yaml`, `assert_ai/library/behaviors/prompt_injection.yaml`, and `assert_ai/library/behaviors/personal_data_privacy.yaml`.