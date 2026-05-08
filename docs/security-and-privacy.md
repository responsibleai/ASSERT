# Adaptive Eval (P2M) Security & Privacy Review

> **Status:** Draft v1, May 8, 2026. Prepared for AI Platform Security and Compliance office hours review (release-gate rows 42-43 on Chang's workback).
>
> **Author:** Jake Present
> **Workstream owner:** Mohamed Elmergawi
> **Companion repo:** [microsoft/adaptive-eval](https://github.com/microsoft/adaptive-eval)
> **Modeled on:** Agent Shield's `docs/security-model.md` (Liam Crumm), with scope reduced to reflect that adaptive-eval is an offline eval pipeline rather than a runtime guardrail.

This document describes the security model for Adaptive Eval (also called P2M): what it protects, which components are trusted, which attacker capabilities are in scope, and which residual risks remain after correct integration. The model applies to the v1 (Build, June 2, 2026) local SDK distribution. Hosted/service deployment is fast-follow and explicitly out of scope.

## 1. Mission And Scope

Adaptive Eval is a **requirement-driven adversarial evaluation pipeline** for AI agents. A customer points P2M at their agent; P2M generates a behavior taxonomy from a plain-English spec, synthesizes adversarial test cases, runs them against the agent in an adapting auditor loop, and uses an LLM judge to score whether the agent satisfied the spec.

The pipeline runs in five stages: **policy** → **design** → **seeds** → **rollout** → **judge**, with all artifacts persisted as local JSON/JSONL files. There is no service component. There is no runtime gating. P2M does not block, modify, or validate any production agent traffic.

**What this review covers:**

- The local Python SDK distributed via the `microsoft/adaptive-eval` repository.
- The threat surface introduced by P2M itself when run against a customer agent on customer infrastructure.
- The privacy and data-flow surface created by the artifacts P2M reads, writes, and forwards to LLM providers.

**What this review does not cover:**

- Hosted or service-mode deployment of P2M (post-v1 fast-follow).
- The customer's agent under test - that's *what is being evaluated*. P2M is supposed to surface its weaknesses, not defend it.
- The customer's LLM provider (Azure, OpenAI, Anthropic, etc.) or its key management.
- The customer's deployment platform, filesystem, container runtime, CI/CD, or package registry.
- Compliance certification by itself. P2M can produce evidence; certification depends on the full system and operating process.

**Posture: P2M is offensive software.** It generates attacks, synthesizes adversarial inputs, and consumes attacker-controlled content (tool outputs, retrieved documents, MCP responses) as evidence. The threat model below reflects that asymmetry.

## 2. Security Objectives

The primary security objectives for Adaptive Eval are:

- **Confidentiality of customer data**: transcripts, scores, traces, and seeds may contain customer PII, secrets, or business-confidential content. P2M must not exfiltrate that data outside paths the customer explicitly configures (LLM provider, OTel collector, local artifacts).
- **Integrity of evaluation results**: adversarial content surfaced through the agent's tool outputs must not be able to bias judge verdicts in ways that disguise real failures or fabricate fake ones.
- **Local-only artifact handling**: no telemetry, no remote logging, no auto-upload of run output. Customer chooses every external sink.
- **Fail-loud on configuration errors**: missing credentials, malformed configs, or unreachable providers should surface clearly rather than silently degrading the run.
- **Bounded resource use**: a customer-initiated run should not be able to exhaust the customer's LLM provider budget without warning.

These objectives apply only to supported usage paths. Customers running P2M against their own agents on their own infrastructure are responsible for the confidentiality of that agent's data, the trust posture of their LLM provider, and the security of their development environment.

## 3. Protected Assets

When customers route agent activity through P2M, the following assets are intended to be protected:

- **Customer agent IO**: transcripts, prompts, tool outputs, retrieved content. Often contains user PII, business-confidential data, internal tool schemas, or credentials accidentally surfaced by buggy agents.
- **Customer LLM provider credentials**: API keys for Azure/OpenAI/Anthropic/etc., read by P2M from environment variables or `.env` files and forwarded to LiteLLM.
- **Customer behavior spec**: the `concept` markdown file that drives the policy stage. May describe sensitive product behavior, internal failure modes, or adversarial scenarios the customer doesn't want public.
- **Generated test cases (`seeds.jsonl`)**: become attack patterns. If committed or shared, those attacks become public knowledge of how P2M probes systems with the customer's spec.
- **Run artifacts (`scores.jsonl`, computed metrics)**: aggregate evidence of where the agent under test failed. Can reveal product weaknesses to anyone who reads them.
- **Audit/trace evidence used by the judge**: spans captured during rollout and consumed by the judge as grounding. Often includes raw tool output that the judge cites verbatim in verdict reasoning.
- **Decision integrity**: the binding between the test seed, the executed rollout, the captured trace, and the rendered verdict. A reviewer reading `scores.jsonl` should be able to trust that the verdict actually corresponds to the cited evidence.

## 4. Principals And Components

A typical P2M deployment has the following principals and components:

- **Customer developer / security policy author**: writes the `concept.md` spec, picks the model deployments, configures rollout against their target agent.
- **P2M CLI/SDK**: the local Python package run from the customer's machine. Reads `.env`, executes the five stages, writes artifacts to `artifacts/results/`.
- **Customer agent (target)**: the system under test. Reached via Python callable, HTTP endpoint, hosted model via LiteLLM, or OTel trace import. Runs in-process with P2M (callable case) or out-of-process (HTTP, hosted, OTel).
- **Auditor LLM**: a P2M-controlled adversarial probe that adapts its inputs to the agent across multiple turns. Same LLM provider as the stage models by default.
- **Judge LLM**: a (typically stronger) LLM that reads transcripts and traces and produces structured verdicts. Pydantic-typed output via LiteLLM.
- **LiteLLM provider routing**: shared transport for all LLM calls. Forwards to Azure OpenAI, OpenAI, Anthropic, etc., based on customer configuration.
- **Customer's tool / MCP servers**: called *live* during rollout. Real APIs, real backends, real side effects unless the customer arranged a mock/sandbox.
- **OpenTelemetry collector**: optional. Phoenix is the reference implementation as a soft dependency. Generic OTel exporters are also supported. P2M ingests spans and feeds them to the judge as evidence.
- **Local file system**: `artifacts/results/<suite>/<run>/` tree. Holds suite-level artifacts (`policy.json`, `seeds.jsonl`) and run-level artifacts (`transcripts.jsonl`, `scores.jsonl`).
- **Build and release systems**: Microsoft GitHub org, CI workflows, package registries. Will produce trusted P2M release artifacts after the OSS hygiene workstream lands.

## 5. Trust Boundaries

P2M sits at the intersection of several distinct trust boundaries:

- **Customer prompts → P2M policy stage**: trusted side. The customer authors the behavior spec.
- **Auditor LLM ↔ customer agent**: bidirectional adversarial loop. Both sides are partially trusted: the auditor is P2M-controlled but its prompts are influenced by the agent's prior responses; the agent's responses are influenced by attacker-controllable content surfaced through tools.
- **Customer agent ↔ tools / MCP servers**: tool outputs are **untrusted**. They can carry attacker-controlled instructions, malformed data, oversized content, or schema-confusing payloads.
- **Tool outputs → OTel traces → judge prompt**: this is the **headline indirect-prompt-injection surface for adaptive-eval**. Attacker content travels through the agent into the trace, the judge reads the trace as evidence, and the judge LLM is now consuming attacker-controlled instructions as part of its scoring context.
- **LiteLLM provider routing**: every LLM call (policy, design, seeds, auditor, judge) flows through LiteLLM to whichever provider the customer configured. Prompt content - including agent-generated content that may contain customer data - leaves the local machine on every call.
- **Local artifacts ↔ customer file system**: filesystem trust depends entirely on the customer's machine. P2M does not encrypt artifacts at rest.
- **Generated seeds → committed repo**: if a customer commits `seeds.jsonl` to a shared repo, attack patterns informed by their behavior spec become readable by everyone with repo access.
- **CI/CD → P2M release artifacts**: the published P2M wheel becomes a trusted runtime component for customers. Supply-chain controls on the release pipeline matter.

## 6. Trusted Computing Base

The trusted computing base is the set of components that must behave correctly for P2M's evaluation results to be trustworthy. Compromise of any TCB component invalidates the run.

**Always in the TCB:**

- The P2M Python package itself (CLI + `p2m/runner.py` + `p2m/stages/*` + `p2m/core/judge.py`).
- The customer's local Python runtime (interpreter, installed dependencies, `.env` parser).
- The LiteLLM library and its routing logic.
- The customer's LLM provider credentials and the network path to the provider.
- The customer's machine (filesystem, process integrity).

**Conditionally in the TCB:**

- The judge LLM, when its verdict is treated as authoritative for downstream decisions.
- The OTel collector, when its captured spans are used as judge evidence.
- The customer's tool / MCP servers, when their live outputs flow into traces.
- Build and release systems (GitHub Actions, package registries, signing infrastructure) for any customer using a published P2M release rather than a local build.

**Explicitly outside the TCB:**

- The customer's agent under test. P2M is supposed to surface its weaknesses; assuming it correct would defeat the purpose.
- Tool / MCP outputs. Those are attacker-influenceable data, not trusted instructions.

## 7. Attacker Model

P2M assumes attackers may be:

- **External users of the customer's agent**: benign-looking but malicious prompts that surface during the auditor probe.
- **Compromised user accounts** in the customer's product.
- **Malicious content authors** whose content is retrieved by the agent under test.
- **Compromised or malicious tool / MCP server operators** who control responses returned to the agent during rollout.
- **Malicious package authors** in the customer's Python dependency tree (this is a generic supply-chain concern, not P2M-specific).

Attackers may control or influence:

- The content of documents, web pages, search results, and other retrieved context the agent under test reaches during rollout.
- The output of MCP servers and tool calls invoked during rollout.
- Tool-call arguments and final responses produced by the agent (these flow into traces and judge prompts).
- Span attributes, event payloads, and embedded text in OTel traces emitted by the agent or its OpenInference instrumentation.
- Network availability of LLM providers, OTel collectors, and tool servers (denial of service via downstream).

**Attackers do NOT have:**

- Direct access to the customer's machine, filesystem, or P2M process memory.
- The ability to modify P2M's policy/design/seed/rollout/judge implementation.
- Control over the customer's LLM provider or its responses (LLM responses can still be wrong, but that's untrusted-output, not attacker-controlled).
- Control over the customer's behavior spec, factor list, or judge dimensions.

Compromise of any of those last three is treated as customer-platform compromise and falls outside P2M's scope.

## 8. In-Scope Threats

P2M is intended to either mitigate or surface the following threats. For each, the primary control and residual gap are noted.

### 8.1 Indirect Prompt Injection via Tool Output → OTel Trace → Judge (HEADLINE)

This is the most distinctive threat for adaptive-eval. The attack chain:

1. Auditor sends an adversarial prompt to the customer agent.
2. Agent calls a tool (search, MCP server, retrieval API).
3. Tool returns content the attacker controls (a poisoned document, a search result, a malicious web page).
4. Tool output flows into the agent's reasoning and into the OTel trace.
5. Trace is imported into P2M and passed as evidence to the judge.
6. Judge LLM consumes the trace - including the attacker-controlled tool output - as part of its scoring context.
7. Attacker has now successfully injected instructions into the judge's prompt. They can attempt to bias the verdict, fabricate evidence citations, or exfiltrate other parts of the prompt context through the judge's response.

**P2M mitigations today:**
- Trace compression keeps only first/last LLM events per node and tool calls (evidence). This reduces the attack surface but explicitly preserves attacker-controlled content because tool outputs are the evidence.
- Judge prompts use Pydantic-typed structured output, which constrains the response shape.
- Judge runs on a stronger model (e.g. `gpt-5.4`) than the auditor, which is more robust to common jailbreak patterns but not immune.

**Residual gaps:**
- No input sanitization, redaction, or provenance tagging on tool output before it's handed to the judge.
- Judge has no way to distinguish "agent reasoning" from "attacker-supplied content" within a transcript.
- Structured output constrains the *response shape* but not the *verdict's correctness* under prompt injection.

### 8.2 Auditor Escalation Off-Scope

The auditor LLM is a P2M-controlled adversarial probe. It generates the next user turn based on the agent's prior responses. If the agent under test returns content crafted to manipulate the auditor (or simply unusual phrasings the auditor pattern-matches on), the auditor can escalate beyond the original behavior spec. Concretely: the auditor could begin probing for vulnerabilities outside the spec, surface attack patterns the customer didn't sign off on, or produce transcripts that look like jailbreak demonstrations rather than spec-compliance evidence.

**P2M mitigations today:**
- Auditor temperature defaults to `0.0` for deterministic probing strategy. (Note: `gpt-5.4-1` deployments do not support `temperature=0.0`; non-determinism cannot be ruled out for that deployment until the limitation is patched.)
- `max_turns` cap (default 6) bounds escalation depth.
- Auditor prompts are derived from `policy.json` behaviors, which are derived from the customer's spec.

**Residual gaps:**
- No content filter on auditor prompts before they reach the agent.
- No detection that auditor has gone off-policy mid-conversation.

### 8.3 Sensitive Data in Local Artifacts

Transcripts, traces, and scored verdicts may contain customer PII, secrets, internal tool schemas, business-confidential strategy, or content the customer's agent accidentally surfaced. These artifacts persist on the customer's filesystem under `artifacts/results/<suite>/<run>/` and are read freely by the local viewer, the `p2m results` CLI commands, and any downstream tooling.

**P2M mitigations today:**
- Artifacts are local-only by default. No telemetry, no auto-upload, no remote sink.
- The classified `LLM*Error` paths suppress full prompt bodies in error messages by default; verbose mode is gated behind `P2M_VERBOSE_ERRORS=1`.

**Residual gaps:**
- No automated PII or secret redaction in transcripts or scores.
- No at-rest encryption.
- No retention policy or expiry; artifacts persist until manually deleted.
- Local file permissions follow the customer's umask. P2M does not enforce restrictive ACLs.

### 8.4 Credential and Prompt Leakage Through LiteLLM Provider Routing

Every LLM call (policy, design, seeds, auditor, judge) flows through LiteLLM to the customer's configured provider. Customer API keys are loaded from environment or `.env`. Prompt bodies on each call carry the agent's content - including content surfaced from attacker-controlled tools.

**Threats:**
- API keys logged into stack traces or error messages on connection failure.
- Prompt bodies (with embedded customer data) crossing into provider-side logging, retention, or fine-tuning datasets per provider policy.
- Auditor / judge prompts containing customer behavior spec (which may itself be sensitive product strategy).

**P2M mitigations today:**
- LLM error classification (`LLMConnectionError`, `LLMAuthError`, etc.) produces a clean one-liner. Full traceback - which can include LiteLLM internals that may reference auth headers - is gated behind `P2M_VERBOSE_ERRORS=1`.
- `.env` is documented as development-only; production usage should use secret management.
- LiteLLM is treated as TCB; customer should pin a known version.

**Residual gaps:**
- No automated key redaction in tracebacks even with verbose mode off.
- No documented guidance on which providers' data-handling policies are appropriate for which customer data classifications.
- No way to mark a behavior spec as "do not send to provider X."

### 8.5 Real-World Side Effects from Rollout

P2M's rollout stage calls the customer's agent live. If the agent is wired to real tools - book a flight, send an email, execute a wire transfer, write to a production database - P2M's adversarial probing can trigger real-world side effects.

**P2M mitigations today:**
- Documentation recommends running rollout against agents wired to mock tools or sandboxed backends.
- The auditor's `max_turns` cap bounds how many tool invocations an auditor can drive per scenario.

**Residual gaps:**
- P2M does not provide a sandbox.
- P2M cannot detect that a tool has real-world side effects.
- Cost exhaustion is its own subcase: even without side effects, large rollouts at 10k seeds × strong judge model can run up significant LLM provider spend on the customer's account. P2M has no built-in budget cap or token-spend telemetry.

### 8.6 Cache Poisoning Across Runs

P2M caches stage outputs by config hash. After a schema or terminology change (e.g. `policy.behaviors` → `policy.behavior_categories`), stale suite-level artifacts can silently feed wrong-shape data to downstream stages. Workaround today: `--force-stage` cascade.

**P2M mitigations today:**
- `--force-stage` flag with cascade semantics (forcing an upstream stage forces downstream stages).
- Cache-skip logic checks file existence and config hash.

**Residual gaps:**
- No `schema_version` sentinel in cached artifacts. Schema drift between cached output and expected input is not auto-detected.
- No automatic invalidation when P2M is upgraded across schema-changing minor versions.

### 8.7 Denial of Service / Cost Exhaustion

An attacker (or, more realistically, a malformed customer config) can drive P2M into expensive states: large `behavior_count`, very long `max_turns`, oversized prompt bodies that the judge has to chunk, or pathological policy configs. For hosted classifiers and judge LLMs, this is also direct cost exhaustion against the customer's provider account.

**P2M mitigations today:**
- Hard caps: `behavior_count` capped at config-time, `max_turns` capped at config-time.
- Stage timeouts are honored by LiteLLM but not P2M-enforced.

**Residual gaps:**
- No global budget cap.
- No per-run cost telemetry surfaced to the customer.
- Prompt size limits depend on the LLM provider; P2M does not pre-validate.

## 9. Out-Of-Scope Threats And Non-Goals

Adaptive Eval does not by itself protect against:

- Direct compromise of the customer's machine, filesystem, container runtime, or development environment.
- A malicious or compromised LLM provider (Azure, OpenAI, Anthropic, etc.).
- A malicious or compromised customer agent under test - P2M is intended to surface that agent's weaknesses, not to defend it.
- A malicious or compromised customer-side tool or MCP server, beyond what's described in §8.1 / §8.5.
- A malicious or negligent policy author (customer side) who writes weak specs.
- Network-level attacks on the LLM provider, OTel collector, or tool/MCP servers.
- Confidentiality from the LLM providers, OTel backends, or any external sink the customer explicitly configures.
- General LLM correctness or hallucination, except as it affects auditor or judge behavior.
- Compliance certification by itself.
- Protecting customer secrets that the customer's own agent surfaces in plaintext during rollout (P2M can record what the agent did, but it's a faithful eval pipeline, not a redaction layer).
- Telemetry leakage. P2M makes no guarantees about data the customer chooses to forward to LLM providers, OTel collectors, or remote logging endpoints.

## 10. Security Guarantees

When the customer correctly integrates P2M and configures it through supported paths, the following are intended to hold:

- **Local-only artifact persistence**: P2M writes only to the `artifacts/results/` tree (and any explicitly configured paths). It does not phone home, upload to remote services, or emit telemetry.
- **No automatic LLM provider switching**: P2M uses the provider the customer configured for each stage. It does not silently fall back to a different provider on error.
- **No automatic credential exfiltration**: API keys are read from environment / `.env` by LiteLLM and forwarded only to the configured provider.
- **Deterministic dispatch**: each stage's invocation is deterministic for fixed inputs (modulo LLM stochasticity at the call sites). Re-running a stage with the same config and same inputs produces the same artifact paths.
- **Fail-loud on auth/connection errors**: LLM provider auth or connection failures classify into typed exceptions and surface a clean error rather than silently producing empty results.
- **Decision integrity (best effort)**: the judge's verdict cites evidence drawn from the run's actual transcripts and traces. There is no automatic "fill in plausible verdict" path.

These guarantees apply only to supported usage paths. Customers must not interpret P2M as a sandbox for their agent under test, as a runtime guardrail, or as a replacement for their own data-protection controls.

## 11. Required Integration Invariants

To preserve the security model, customer integrations must satisfy:

- **Customer agents under test that have real-world side effects must be sandboxed or wired to mocks.** P2M will drive adversarial inputs at whatever the rollout target points at; if that target executes real wire transfers, P2M will trigger real wire transfers.
- **API keys must come from the customer's secret management.** `.env` is for development only.
- **Customer must review the LLM provider's data-handling policy** before sending behavior specs, transcripts, or trace content through P2M. Specifically, customers handling regulated data should verify the provider does not retain prompts, log them for moderation, or use them for fine-tuning.
- **Run artifacts must be treated as containing customer data.** Filesystem ACLs, retention, and cleanup are the customer's responsibility.
- **Behavior specs and seed sets that contain sensitive product strategy should not be committed to public repositories.**
- **OTel trace ingestion should be reviewed for sensitive content** before pointing the judge at a real run. Tool outputs in traces commonly contain customer PII.
- **Judge model selection should match data sensitivity.** A judge running on a less-trusted provider sees the full transcript including any sensitive content.
- **Customer should pin P2M and LiteLLM versions** for reproducible runs; both are TCB.

## 12. Residual Risks

Even with correct integration, residual risks remain:

- **Indirect prompt injection through tool output → trace → judge** is partially mitigated only. There is no input sanitization between trace ingestion and judge prompt.
- **No automated PII / secret redaction** in transcripts, scores, or traces.
- **No at-rest encryption of artifacts.**
- **No built-in budget cap.** Large runs can consume significant LLM provider spend.
- **Judge correctness is best-effort.** Verdicts can be wrong even on uncontested transcripts due to LLM stochasticity. Adversarial tool output can produce confident-but-wrong verdicts.
- **Run-to-run verdict variance** is meaningful. Single-run verdicts should not be treated as ground truth; high-confidence signal requires multiple runs and either paired statistical comparison or labeled ground truth.
- **Cache schema drift** between P2M minor versions can produce wrong-shape downstream artifacts. Workaround is `--force-stage`.
- **Auditor non-determinism on `gpt-5.4-1`** (and any deployment that does not support `temperature=0.0`).
- **Cost exhaustion** from large or pathological runs is unbounded. Customer must monitor provider-side spend.
- **Operators may treat P2M results as compliance evidence** without paired ground truth or statistical significance. P2M's output shape is more informative than its statistical validity.

## 13. Threat-To-Control Matrix

| Threat | Primary controls | Required customer assumption | Residual risk |
|---|---|---|---|
| Indirect prompt injection (tool → trace → judge) | Trace compression, Pydantic-typed judge output, stronger judge model | Trace content is reviewed; judge model is robust to common jailbreak patterns | No input sanitization on tool output; verdict bias under prompt injection |
| Auditor escalation off-scope | `temperature=0.0` (when supported), `max_turns` cap, behavior-derived prompts | Auditor model is reasonably aligned to its system prompt | Auditor can drift mid-conversation; non-determinism on `gpt-5.4-1` |
| Sensitive data in artifacts | Local-only persistence, `P2M_VERBOSE_ERRORS=0` default | Customer enforces filesystem ACLs and retention | No redaction, no encryption |
| Credential / prompt leakage via LiteLLM | LLM error classification, secret-management guidance | Customer uses secret manager in production; reviews provider data policy | No automated key redaction; no per-spec provider gating |
| Real-world side effects from rollout | Documentation, `max_turns` cap | Customer sandboxes or mocks tools with real-world effects | P2M does not provide sandbox; cannot detect side-effecting tools |
| Cache poisoning across runs | Config-hash invalidation, `--force-stage` cascade | Customer re-runs with `--force-stage` after upgrades | No `schema_version` sentinel; silent stale-cache feeds possible |
| Denial of service / cost exhaustion | Config-time caps on `behavior_count` and `max_turns` | Customer monitors provider-side spend | No global budget cap; no per-run cost telemetry |
| Decision-integrity drift | Stage caching by config hash, deterministic dispatch | Customer does not edit cached artifacts manually | Manual artifact tampering is not detected |
| Supply-chain compromise | Microsoft GitHub org, signed releases (planned), pinned `microsoft/adaptive-eval` versions | Customer pins versions; CI/CD is hardened | OSSF scorecard + ComponentCheck still in flight; supply-chain hardening is on the OSS-hygiene punch list |

## 14. Open Questions For Reviewers

These are explicitly flagged for the AI Platform Security and Compliance review and any subsequent ad-hoc engineering follow-up:

1. **Should P2M provide PII / secret redaction by default** in transcripts and scores? Pro: meaningfully reduces residual risk in §8.3. Con: redaction is lossy and judges scoring on redacted transcripts may produce wrong verdicts. Customer-side redaction is also an option.
2. **What is the recommended trust posture for the judge model?** A judge that consumes attacker-controlled tool output should arguably run on a model with stronger jailbreak robustness, but customers may want flexibility. Should we publish a recommended-judge-model list?
3. **What is the recommended customer-facing guidance on tool sandboxing?** Today this is informal documentation. Should it be a hard precondition called out in the README, the policy authoring guide, or both?
4. **Should the v1 SDK ship a default budget cap?** Mohamed's load-testing criterion (May 6 standup) implies large runs are expected; Chang's $2K test-budget excludes load-testing. Customer-side budget cap is straightforward to add but pushes a behavior change.
5. **Local-vs-production-flag question (Mohamed's May 7 ask).** Adaptive Eval's answer is "no, the flag does not apply." P2M is not in the dev inner loop; customers run it intentionally as an offline evaluator. The Agent-Shield-style inner-loop bypass concept is not relevant for an offline pipeline. Confirm this read with the reviewer.
6. **Provenance and signing for v1 release artifacts.** OSSF scorecard, ComponentCheck pipeline, and signed-release plumbing are on the OSS hygiene punch list but not yet in place. What is the security expectation for the v1 release artifact specifically?
7. **OTel auto-instrumentation as a soft dependency.** Phoenix is the reference UX. Customers can also use generic OTel exporters. Should we document the expected trust posture of the OTel collector for customers handling regulated data?
8. **Behavior spec confidentiality.** Specs flow through every stage's LLM call. Customers handling regulated content may need a way to keep the spec local while still running rollout against a hosted-model agent. Out of v1 scope, but worth raising.

## Appendix A: Sources

- Agent Shield `docs/security-model.md` (Liam Crumm) - structural template.
- May 1 production-readiness audit (Jake) - feeds §8.3 / §8.4.
- May 6 standup notes (Mohamed publicly named "Jake's load testing" as a stated criterion) - feeds §8.7 / §14.4.
- May 7 standup notes (Mohamed asked Jake to think about a local-vs-prod flag) - answered in §14.5.
- May 7 Liam-Jake sync notes - the indirect prompt injection threat in §8.1 is partially derived from Liam's threat-model carryovers.
- May 8 perf baseline (`p2m-verification/2026-05-08-perf-baseline.md`) - feeds §8.7 cost projections.
- `p2m/runner.py`, `p2m/stages/*`, `p2m/core/judge.py` in `microsoft/adaptive-eval` (current `main`).

## Appendix B: Glossary

- **P2M**: internal name for adaptive-eval. The CLI binary is `p2m`.
- **Suite**: a named dataset (`policy.json` + `seeds.jsonl` + `design.json`) shared across multiple runs.
- **Run**: a specific execution against a suite, producing `transcripts.jsonl` + `scores.jsonl`.
- **Auditor**: P2M-controlled adversarial LLM that drives multi-turn probing.
- **Judge**: structured-output LLM that scores transcripts against the policy.
- **Concept**: customer-authored markdown file describing what the agent should and should not do.
- **Stage**: one of `policy`, `design`, `seeds`, `rollout`, `judge`. Pipeline executes them in order.
