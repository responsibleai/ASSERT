# Travel Planner — Quality Benchmark

A throughput/scale benchmark variant of the flagship `travel_planner_langgraph` example —
**not** a new agent. Same target (`examples.travel_planner_langgraph.agent:chat_sync`), same
tool servers, different purpose: measure inference/judge throughput on realistic,
non-adversarial traffic rather than probe for safety failures.

## Why this is a separate config, not a `behaviors/*.yaml` sibling

`travel_planner_langgraph/behaviors/*.yaml` each measure one atomic **safety** or
**quality-mechanism** behavior (tool selection, grounding, constraints, verification,
stereotyping, sycophancy, prompt injection) against a shared application `context:`, per the
one-behavior-one-config pattern in [best practices §8.D](../../docs/config/best-practices.md).

This config uses `explicit_constraint_violation_failures` — the same atomic preset one of those
siblings already uses (`behaviors/constraints.yaml`) — so it is **not a new behavior**. What
makes it a distinct example is the `context:`: it deliberately asks the tester to generate
**realistic, non-adversarial** requests only, omitting the adversarial/safety-themed generation
axes (prompt-injection probing, jailbreak attempts, sycophancy bait, stereotyping prompts) that
the flagship example's `context:` invites. That keeps every generated test case "in-distribution"
customer traffic, which is what a throughput benchmark needs — an adversarial mix would conflate
scale-testing with safety-testing and make the numbers unusable for either purpose.

See [`travel_planner_benchmark.md`](travel_planner_benchmark.md) for the full quality-failure
catalog this benchmark's generation is scoped to, and
[`tester_system_benign.md`](tester_system_benign.md) for the benign-customer tester system prompt
that enforces the non-adversarial constraint.

## Run it

```bash
assert-ai run --config examples/benchmark/eval_config.yaml
```

Judged on `policy_violation` (explicit-constraint violations) and `overrefusal`, same headline
pair as the rest of `examples/`. `pipeline.test_set.scenario.sample_size: 10` and
`pipeline.inference.concurrency: 10` are set higher than the flagship example's defaults — this
config exists specifically to exercise the pipeline at that scale, not to explore behavior depth.

## Run it alongside the flagship example

Because this shares the flagship's target and one of its atomic behaviors, the two are meant to
be read together, not chosen between:

```bash
# Full behavior depth (7 atomic behaviors, adversarial + quality)
assert-ai run --config examples/travel_planner_langgraph/behaviors/constraints.yaml

# Throughput benchmark at scale (1 behavior, realistic non-adversarial traffic only)
assert-ai run --config examples/benchmark/eval_config.yaml
```

If you're evaluating a new agent for the first time, start with
[`travel_planner_langgraph/README.md`](../travel_planner_langgraph/README.md) instead — this
example is a scale/throughput variant for when you already have that working.
