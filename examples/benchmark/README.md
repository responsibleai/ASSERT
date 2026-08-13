# Travel Planner — Quality Benchmark

A throughput/scale benchmark variant of the flagship `travel_planner_langgraph` example —
**not** a new agent. Same target (`examples.travel_planner_langgraph.agent:chat_sync`), same
tool servers, different purpose: measure inference/judge throughput on realistic,
non-adversarial traffic rather than probe for safety failures.

## Why this is a separate benchmark config

This config measures one behavior: the library preset
[`explicit_constraint_violation_failures`](../../assert_ai/library/behaviors/explicit_constraint_violation_failures.yaml).
It is **not** a new behavior. What makes it a distinct example is the `context:`:
it deliberately asks the tester to generate **realistic, non-adversarial**
requests only, omitting prompt-injection probes, jailbreak attempts,
sycophancy bait, and stereotyping prompts. That keeps every generated test
case representative of customer traffic, which is what a throughput benchmark
needs. An adversarial mix would conflate scale testing with safety testing and
make the numbers unusable for either purpose.

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

## Compare it with the flagship example

Because this shares the flagship's target, read the two together rather than
choosing between them. Start with the focused behavior configs linked from
[`travel_planner_langgraph/README.md`](../travel_planner_langgraph/README.md),
then use this benchmark when you want one realistic behavior exercised at
higher sample size and concurrency.
