---
title: Triage Agent — eval-fix loop
description: Adaptive Eval plus AgentShield in a closed loop. Discover behavior categories, author a runtime guardrails contract, re-evaluate to prove the fix landed.
---

An incident-triage agent demonstrates the **local-first dev eval-fix loop**: write an agent → run Adaptive Eval to find its behavior categories → close the runtime-fixable ones in an AgentShield `.guardrails.yaml` → re-run Adaptive Eval to prove the fixes landed without regression.

This is the only end-to-end OSS story that closes the loop locally. Most eval tools find failures; most runtime governance tools enforce policy; very few do both, on the same agent, with the same test suite as the verification.

## Source

```
examples/incident_triage_agent/
├── SOP.md                                # the company's incident-triage spec
├── incident_triage_workflow_failures.md  # behavior-category definition file
├── agent.py                              # baseline agent (LangGraph)
├── agent_guarded.py                      # same agent wrapped with AgentShield guards
├── eval_config_baseline.yaml             # Adaptive Eval config: BEFORE run
├── eval_config_guarded.yaml              # Adaptive Eval config: AFTER run, same test cases
├── incident-triage.guardrails.yaml       # AgentShield contract — the closures
└── fixtures/                             # test data (incidents, teams)
```

## The loop

```
write agent ──► p2m run (BEFORE)  ──► find behavior categories
                                          │
                                          ▼
                              author .guardrails.yaml
                                          │
                                          ▼
                                 agent_guarded.py
                                          │
                                          ▼
                              p2m run (AFTER, same test cases)
                                          │
                                          ▼
                          per-mode statistical closure verdict
```

## What you'll see

- **Eleven behavior categories** derived from the SOP — channel-severity binding, alert-ID consistency, XPIA-relay, pager-severity match, escalation rules, etc.
- **AgentShield contract** authoring — ~80 lines of YAML, one gate per runtime-fixable mode.
- **Statistical closure verdicts.** At n=200 test cases per rail, the paired BEFORE/AFTER comparison uses a 2-proportion z-test (α=0.05) to mark each mode as ✅ closed, ⚪ no significant change, or 🔴 regression.

## Headline result (sample)

5 of 6 runtime-fixable scenario-rail behavior categories are statistically significantly closed. Two residual modes — `wrong_severity` and `fabrication` — move within noise. **That's the design.** Runtime guards can't fix model-judgment failures; the loop surfaces those back to the developer for prompt or model work.

## Run it

```bash
# BEFORE
p2m run --config examples/incident_triage_agent/eval_config_baseline.yaml

# author incident-triage.guardrails.yaml (or read the example)

# AFTER (same test cases)
p2m run --config examples/incident_triage_agent/eval_config_guarded.yaml
```

## Why this matters

This is the demo most enterprise customers want to see: a fix that's not just "we updated the prompt" but a **policy artifact** (the `.guardrails.yaml`) with a **measurable closure proof** (paired statistical verdict on identical test cases). The eval, fix, and verification are all in the same project.

The full case study with per-mode analysis lives in the example's `README.md` and `docs/case-study-incident-triage-joint.md` on the source branch.
