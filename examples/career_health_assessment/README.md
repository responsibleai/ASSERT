# Career Health Assessment — Clarity → ASSERT → ACS → ASSERT

A self-contained replication package for evaluating and governing the bounded
career-health assessment agent. It follows the full loop: discover risks with
**Clarity**, measure a **baseline** with ASSERT, generate and commit an **ACS**
output gate, and **re-measure** the governed agent to show the delta.

## The agent

[`agent.py`](agent.py) is a bounded, single-turn prompt agent over synthetic CV
text. It has **no tools** and returns **structured JSON only**. Each call runs
one `TASK`: `cv_parsing`, `narrative_assessment`, or `cv_quality_evaluation`. Its
entire behavioral contract lives in one system prompt: assert only facts stated
in the source, cite exact-span evidence, stay bounded and qualified, treat CV
text as untrusted data, and refuse unsupportable input. Because every guardrail
is prompt-only, it is defeasible under adversarial or ambiguous input.

Since the agent has no tools, **every ACS gate here is an output-semantic
annotator gate** over the reply text (there is nothing to gate structurally).

## Risks measured

Clarity ([`Clarity Protocol/`](Clarity%20Protocol/)) surfaced five failure modes;
the two highest-value were triaged for measurement:

| Risk | Clarity severity | Gate |
|---|---|---|
| **Fabricated / unsupported inference** | Critical | output annotator: deny any asserted fact, score, or evidence span not grounded in the supplied source |
| **CV-embedded prompt injection** | High | output annotator: deny a reply that obeys an instruction embedded in CV_TEXT, or returns a favorable `status: ok` without signalling the injection |

Each risk is an **atomic** eval (one behavior per config), run as a baseline →
governed A/B.

## Results (n=25 per test type; agent `azure/gpt-4o-mini`, judge & annotator `azure/gpt-5.4`)

**Harm** = `not_permissible_policy_violation_rate` (a non-permissible behavior was
violated). Overrefusal is reported separately — it is a different problem.

### Fabricated / unsupported inference

| Metric | Prompt (base → gov) | Scenario (base → gov) |
|---|---|---|
| **Harm (non-permissible)** | **81.0% → 46.2%** | **28.6% → 21.4%** |
| Permissible-behavior violated | 58.3% → 48.0% | 48.0% → 36.0% |
| Overrefusal | 20.0% → 44.0% | 48.0% → 36.0% |

Harm nearly halved on prompt and dropped on scenario. The cost is higher
single-turn overrefusal (20%→44%): when the gate denies an ungrounded narrative
and the regeneration still cannot ground it, the agent falls back to
`insufficient_input`. The residual harm is subtle evaluative narrative language
the regeneration still emits — a genuinely hard semantic boundary.

### CV-embedded prompt injection

| Metric | Prompt (base → gov) | Scenario (base → gov) |
|---|---|---|
| **Harm (non-permissible)** | **42.1% → 0.0%** | **25.0% → 0.0%** |
| Permissible-behavior violated | 92.0% → 56.0% | 73.9% → 72.0% |
| Overrefusal | 76.0% → 56.0% | 68.0% → 72.0% |

Injection harm **eliminated** on both test types, and single-turn overrefusal
also fell (76%→56%). On a detected injection the governed agent signals it
(`status: insufficient_input` + `untrusted_instruction_ignored`) while still
grounding the genuine CV facts, which the judge accepts as safe. Multi-turn
overrefusal rose slightly (68%→72%) — the expected tension of forcing the
signalled-safe path.

## Layout

```
agent.py                         # baseline target (chat)
agent_guarded.py                 # governed targets: chat_fabrication, chat_injection
Clarity Protocol/                # archived risk-discovery protocol
evals/<risk>/eval_config.yaml            # baseline
evals/<risk>/eval_config.governed.yaml   # governed (byte-identical except run + target.callable)
acs/<risk>/manifest.yaml         # committed output-gate manifest
acs/<risk>/policy/*.rego         # committed Rego (deny when the output annotator flags)
```

The guarded targets **import** the baseline `chat` verbatim and add only the ACS
gate, so the A/B differs by nothing but the gate. The annotator runs on
`azure/gpt-5.4` — matching the judge — because a weaker annotator is more lenient
than the judge on subtle claims and the gate then never fires on the cases the
judge flags.

## Reproduce

```powershell
# Baselines
assert-ai run --config examples/career_health_assessment/evals/fabricated-inference/eval_config.yaml
assert-ai run --config examples/career_health_assessment/evals/cv-prompt-injection/eval_config.yaml

# Governed (reuses the cached systematize/test_set stages for a true A/B)
assert-ai run --config examples/career_health_assessment/evals/fabricated-inference/eval_config.governed.yaml
assert-ai run --config examples/career_health_assessment/evals/cv-prompt-injection/eval_config.governed.yaml

# Deltas
assert-ai results status career-health-fabricated-inference baseline --json
assert-ai results status career-health-fabricated-inference acs-governed --json
assert-ai results status career-health-prompt-injection baseline --json
assert-ai results status career-health-prompt-injection acs-governed --json
```

Explore transcripts, forest plots, and the permissible-vs-non-permissible split
in the bundled viewer (`cd viewer && npm install && npm run dev`).
