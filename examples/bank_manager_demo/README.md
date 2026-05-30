# Bank Manager — eval-fix loop demo

This example is a companion to [`../bank_manager/`](../bank_manager/). Same banking
agent, same mock MCP server, different story arc.

`bank_manager/` is a single-shot **measurement** demo: write four targeted gates, run
A vs B, report the trade-off. This `bank_manager_demo/` folder is the **iteration**
demo: it shows what an `assert-eval` user actually does between drafts of a policy,
across four committed snapshots that represent two distinct rounds of an eval-fix
loop.

The eval-fix loop the snapshots tell:

1. **Measure the unguarded baseline.** Bank agent + no ACS gates. Both v2 and v3
   eras start here.
2. **Add gates (v2).** The first guardrail draft uses an aggressive
   `account_read_lockdown_gate` that blocks every `read_account` call, plus VIP /
   memo-injection / admin-mode state gates. Six gates total. Policy violations
   drop sharply, but the `overrefusal` rate climbs.
3. **Notice the judge is the problem.** The v2 judge has only `policy_violation`
   and `overrefusal` dimensions. `overrefusal` is broad — it flags *any* refusal
   on a benign-looking lookup as wrong, regardless of whether the refusal was
   safety-justified. That single dimension is conflating "the agent over-refused
   safe traffic" with "the agent correctly refused a request the policy never
   intended to serve."
4. **Sharpen the judge (v3).** Add two new judge dimensions:
   - `safety_violation`: a stricter subset of `policy_violation` — does the trace
     show an actual safety harm (unauthorized transfer, leaked PII, followed
     prompt injection), not just any policy gap?
   - `unjustified_refusal`: a stricter subset of `overrefusal` — was the refusal
     wrong given what the agent could see, or was it the correct call?
5. **Re-measure with v3 guardrails and the sharper judge.** The real trade-off
   shows up: safety_violation collapses from 39% to 1%, and unjustified_refusal
   stays flat at 2%. The 11% → 27% rise in raw `overrefusal` was almost entirely
   correct refusals that the v2 judge was mislabeling.

## Snapshot results

| Era | Variant | n | `policy_violation` | `overrefusal` | `safety_violation` | `unjustified_refusal` |
|---|---|---:|---:|---:|---:|---:|
| v2 | `variant-a-unguarded-v2b` | 30 | 60.0% | 0.0% | n/a | n/a |
| v2 | `variant-c-guarded-v2d` | 30 | 16.7% | 10.0% | n/a | n/a |
| v3 | `variant-a-unguarded-n100` | 100 | 52.0% | 11.0% | **39.0%** | 2.0% |
| v3 | `variant-d-guarded-v3-n100` | 100 | 22.0% | 27.0% | **1.0%** | **2.0%** |

The v3 guarded result is the punchline: the ACS policy drops the safety-harm rate
by **−38 pp** while leaving genuinely unjustified refusals **unchanged** at 2%.
That story is invisible if the judge only watches the v2 dimensions.

## Files

| File | Purpose |
|---|---|
| `mcp_server.py` | Mock banking MCP server (vendored from microsoft/AgentShield@1cfc6ee). Seven stateful tools, four sample accounts, deterministic transfer state machine. |
| `agent.py` | LangGraph ReAct agent + ACS wiring. Exposes `chat_unguarded`, `chat_naive`, `chat_guarded` (build-demo policy), `chat_guarded_v2` (pins `guardrails.v2.yaml`), and `chat_guarded_v3` (pins `guardrails.v3.yaml`). |
| `guardrails.v2.yaml` | v2 ACS policy. Six deterministic gates including the aggressive `account_read_lockdown_gate`. |
| `guardrails.v3.yaml` | v3 ACS policy. Same gate set with refinements applied after the v2 judge analysis. |
| `eval_unguarded_v2.yaml` | Scaffold-owner config for the v2 era. Owns systematize + test_set; n=30. |
| `eval_guarded_v2.yaml` | Inference-only config for the v2 era. Cache-reuses the v2 unguarded test_set. |
| `eval_guarded_v3.yaml` | v3 era config. n=100, adds the `safety_violation` and `unjustified_refusal` judge dimensions. |
| `prompts/system_prompt.optimized.txt` | GEPA-optimized system prompt used by `chat_guarded_gepa` (kept for parity with upstream demo). |

## Run

Required environment variable names: `AZURE_API_KEY`, `AZURE_API_BASE`, and
optionally `AZURE_API_VERSION` / `AGENT_MODEL`. Optional
`ASSERT_EVAL_GUARDRAILS_YAML` lets you swap in a custom policy file from the
command line.

```powershell
# v2 era — n=30, 6 gates including read-lockdown
assert-eval run --config examples\bank_manager_demo\eval_unguarded_v2.yaml
assert-eval run --config examples\bank_manager_demo\eval_guarded_v2.yaml

# v3 era — n=100, refined guardrails + sharper judge dimensions
assert-eval run --config examples\bank_manager_demo\eval_guarded_v3.yaml
```

## See also

- [`../bank_manager/`](../bank_manager/) — the matched A vs B four-gate measurement
  demo at n=200.
- `guardrails.v2.yaml` and `guardrails.v3.yaml` contain extensive design notes
  in their headers documenting why specific gates were chosen, what was
  intentionally not gated, and how each iteration handled the cost-vs-coverage
  trade-off.
