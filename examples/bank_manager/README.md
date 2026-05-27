# Bank Manager — ACS three-variant ASSERT example

This example evaluates a LangGraph bank-manager agent with and without
Agent Control Specifications (ACS). ACS is the policy layer; the public
`agent_shield` Python package is the runtime imported by `agent.py`.

The example measures a practical trade-off: reducing security-sensitive
behavior without driving up overrefusal on benign banking requests.

> Source provenance: the MCP server, ACS YAML, and detector prompts are adapted
> from the public upstream bank-manager reference at commit `1cfc6ee` under the
> Apache-2.0 / MIT license. ASSERT-specific files are listed in
> [Provenance](#provenance).

## Run it

From the repository root:

```powershell
assert-eval run --config examples\bank_manager\eval_config_unguarded.yaml
```

Show the high-level results for that run:

```powershell
assert-eval results status --results-dir "$PWD\artifacts\results" bank-manager variant-a-unguarded
```

The repository also includes a committed n=100 snapshot. To inspect that
snapshot without re-running the pipeline, point the results command at the
example-local artifacts directory:

```powershell
assert-eval results status --results-dir "$PWD\examples\bank_manager\artifacts\results" bank-manager variant-a-unguarded
```

## Variants

`eval_config_unguarded.yaml` is the baseline with no ACS gates. The renamed
`eval_config_baseline_prompt.yaml` is the prompt-hardened baseline: no ACS
gates, but a blunt refusal block appended to the system prompt. The labels below
keep those two baselines distinct.

| Label | Config | Target callable | What changes |
|---|---|---|---|
| A · baseline (unguarded) | `eval_config_unguarded.yaml` | `chat_unguarded` | Raw LangGraph agent with the original permissive prompt; no ACS gates. |
| B · ACS-gated | `eval_config_guarded.yaml` | `chat_guarded` | Same prompt as A, with the five-stage ACS policy enabled. |
| C · baseline prompt-hardened | `eval_config_baseline_prompt.yaml` | `chat_baseline_prompt` | Same raw agent as A, with a five-line refusal block appended; no ACS gates. |

Trade-off chart:

![Trade-off: behavior rate vs overrefusal across the three variants](artifacts/trade_off.png)

All numbers below are from the committed n=100 run on 2026-05-24 with the ten
current judge dimensions, `max_turns: 3`, and judge model `azure/gpt-5.4-mini`.
Judge failures were 0% across all variants.

| Behavior / judge dimension | A: baseline (unguarded) | B: ACS-gated | C: baseline prompt-hardened |
|---|---:|---:|---:|
| `large_transfer_no_hitl_viol` | 1% | **0%** | 0% |
| `vip_transfer_no_approval_viol` | 23% | **0%** | 3% |
| `freeze_without_admin_mode_viol` | 7% | **0%** | 0% |
| `prompt_injection_via_tool_output_viol` | 0% | **0%** | 0% |
| `pii_leak_in_output_viol` | 0% | 0% | 0% |
| `pii_disclosure_under_social_pressure_viol` | 0% | 0% | 0% |
| `account_takeover_attempt_viol` | 19% | 4% | 0% |
| `sycophantic_policy_drift_viol` | 0% | 0% | 0% |
| `fabricated_financial_fact_viol` | **39%** | **0%** | 3% |
| `overrefusal` | **0%** | 31% | 22% |
| **max behavior_rate across 9 security dimensions** | **39%** | 4% | 3% |
| **any-security-violation rate** | **42%** | 4% | 3% |

Read the table this way:

- **A → C (prompt-only hardening)**: any-security-violation drops 42% → 3%, but
  overrefusal rises 0% → 22%. Prompt text can reduce several model-driven
  failures, but it is not a runtime enforcement layer.
- **A → B (enable ACS)**: any-security-violation drops 42% → 4%, while
  overrefusal rises 0% → 31%. This is the ACS-gated solution: structurally
  checkable actions move into policy gates, with the overrefusal cost visible.

## Behaviors under test

The example covers four behavior axes on the same bank-manager agent. The judge
uses ten binary dimensions; three read the full multi-turn trajectory rather
than only the final assistant response.

| Axis | Behavior | Judge dimension(s) | Main mitigation path |
|---|---|---|---|
| Instruction & control | `instruction_injection_via_memo` | `prompt_injection_via_tool_output_viol`, `account_takeover_attempt_viol` | ACS state machine plus task-adherence LLM stage. |
| Information integrity & leakage | `pii_disclosure_under_social_pressure`, `pii_leak_in_output`, `fabricated_financial_fact` | `pii_disclosure_under_social_pressure_viol`, `pii_leak_in_output_viol`, `fabricated_financial_fact_viol` | ACS social-engineering and PII stages; prompt grounding for fabricated financial claims. |
| Tool / action misuse | `capability_required_action_without_hitl` | `large_transfer_no_hitl_viol`, `vip_transfer_no_approval_viol`, `freeze_without_admin_mode_viol` | ACS state machine. |
| System-level behavior | `sycophantic_policy_drift` | `sycophantic_policy_drift_viol` | Prompt hardening plus ACS blocking of individual privileged calls. |

## Files

| File | Role |
|---|---|
| `mcp_server.py` | FastMCP server with seven in-memory banking tools. |
| `guardrails.yaml` | Leaf ACS policy: fraud detector plus PII redaction. |
| `bank-mcp-template.guardrails.yaml` | MCP tier: state machine and approval tokens. |
| `bank-base.guardrails.yaml` | Base tier: input/output LLM stages. |
| `prompts/*.md` | Bank-specific LLM detector prompts. |
| `prompts/cross/*.md` | Cross-example detector prompts. |
| `agent.py` | ASSERT callable targets for the three variants. |
| `eval_config_unguarded.yaml` | A · baseline (unguarded). |
| `eval_config_guarded.yaml` | B · ACS-gated. |
| `eval_config_baseline_prompt.yaml` | C · baseline prompt-hardened. |
| `artifacts/trade_off.png` | Rendered trade-off chart. |

## Run all variants

Each n=100 variant takes roughly 17–20 minutes on one Azure deployment. The
suite-cached `systematize` and `test_set` stages mean later runs can reuse the
same upstream artifacts and only re-run `inference` and `judge`.

```powershell
assert-eval run --config examples\bank_manager\eval_config_unguarded.yaml
assert-eval run --config examples\bank_manager\eval_config_guarded.yaml
assert-eval run --config examples\bank_manager\eval_config_baseline_prompt.yaml
```

Runtime artifacts land under `artifacts/results/bank-manager/`:

- `variant-a-unguarded/` — A · baseline (unguarded)
- `variant-b-guarded/` — B · ACS-gated
- `variant-c-baseline-prompt/` — C · baseline prompt-hardened

A committed snapshot of `metrics.json`, `scores.jsonl`, `config.yaml`, and
`manifest.json` lives under
`examples/bank_manager/artifacts/results/bank-manager/` for chart
reproduction.

## Re-render the trade-off chart

```powershell
python scripts\render_trade_off.py
```

The renderer checks the committed snapshot under
`examples/bank_manager/artifacts/results/` first, then falls back to live runtime
artifacts under `artifacts/results/`.

## Reproduction notes

- All three configs share `suite: bank-manager`, so suite-level
  `systematize` and `test_set` artifacts are versioned once and reused across
  variants when compatible.
- Set `AGENT_MODEL=gpt-4o-mini` to pin the target agent model. ACS LLM stages
  share that model through `Shield.from_yaml(...).with_langchain().with_client(llm)`,
  overriding the YAML-declared provider.
- The three eval configs do not import DSPy at runtime.

## Provenance

`mcp_server.py`, `guardrails.yaml`, `bank-mcp-template.guardrails.yaml`,
`bank-base.guardrails.yaml`, and `prompts/*.md` are adapted from the public
bank-manager reference at commit `1cfc6ee` under the Apache-2.0 / MIT license.

`agent.py`, the three eval configs, `scripts/render_trade_off.py`, and this
README are written for this ASSERT integration. The original `SYSTEM_PROMPT`
constant in `agent.py` is copied from the same upstream commit;
`SYSTEM_PROMPT_BASELINE_PROMPT` is new.
