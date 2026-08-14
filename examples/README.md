# Examples

Runnable configs and sample agents for ASSERT. Start with the LangGraph travel
planner: it uses `target.callable` with OpenTelemetry trace capture so the judge
can inspect tool calls, routing, and intermediate decisions.

## First run

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[otel,langgraph]"
Copy-Item .env.example .env
# Set AZURE_API_BASE and AZURE_API_KEY.

assert-ai run --config examples/travel_planner_langgraph/evals/budget_overrun.yaml
assert-ai results status travel-langgraph-budget-overrun baseline
```

Artifacts are written to
`artifacts/results/travel-langgraph-budget-overrun/baseline/`.

## Create your own config

```powershell
assert-ai init --model azure/gpt-5.4-mini
assert-ai init --model azure/gpt-5.4-mini --from examples/travel_planner_langgraph/evals/budget_overrun.yaml
```

See the [CLI reference](../docs/cli/commands.md#init) for all options.

## Reuse a behavior from the library — check here first

Before writing a `behavior.description` from scratch, check the **[Behavior Library](../assert_ai/library/behaviors/README.md)**
(`assert_ai/library/behaviors/`) — the single source of truth for atomic,
ready-to-use behavior presets shipped with ASSERT. Each preset is scoped to
one mechanism (one judge verdict, one behavioral claim), covering safety,
bias/fairness, and agentic failure modes. Browse the full catalog with:

```powershell
assert-ai library list --kind behavior
assert-ai library show <preset-name>
```

Pair a preset with application context from the **[Scenario Library](../assert_ai/library/scenarios/README.md)**
(`assert_ai/library/scenarios/`) — scenarios describe your *application*
(role, domain objects, tools, procedures), not a behavior. One config per
behavior, sharing a common scenario's `context:`, is the pattern every
example in this directory follows.

## Worked evaluations

Every config below measures one behavior. Each directory README covers the
scenario, setup, run commands, and artifact paths.

| Example | Target shape | Focus |
|---|---|---|
| [`travel_planner_langgraph/`](travel_planner_langgraph/) | LangGraph callable + OTel traces | Grounded itineraries and budget compliance. Recommended starting point. |
| [`travel_planner_neurosan/`](travel_planner_neurosan/) | Custom multi-agent callable + manual OTel spans | Framework-independent trace integration. |
| [`azure_doc_qa/`](azure_doc_qa/) | Multi-agent RAG callable | Confidential-data boundaries and grounded answers. |
| [`billing_support_agent/`](billing_support_agent/) | Tool-using callable | Identity verification and account isolation. |
| [`change_control_agent/`](change_control_agent/) | Workflow callable | Approval sequencing and record integrity. |
| [`science_research_agent/`](science_research_agent/) | Retrieval callable | Sharing classes and retrieved prompt injection. |
| [`incident_triage_agent/evals/`](incident_triage_agent/evals/) | Tool-using callable | Nine independently runnable SOP behaviors. |

## Target and instrumentation galleries

| Example | Purpose |
|---|---|
| [`prompt_agents/`](prompt_agents/) | Prompt Agent target shapes: model-only, simulated tools, sandbox tools, generated tools, and external connector. |
| [`phoenix_auto_trace/`](phoenix_auto_trace/) | Auto-instrumentation across supported agent frameworks, with two atomic LangGraph evals. |
| [`langgraph-foundry-hosted/`](langgraph-foundry-hosted/) | LangGraph target hosted through Foundry. |
| [`azure_managed_identity/`](azure_managed_identity/) | Azure OpenAI authentication with managed identity or `az login`. |

## Specialized demos and shared assets

| Directory | Purpose |
|---|---|
| [`acs_guardrails/`](acs_guardrails/) | Offline ASSERT-to-ACS guardrail generation demo. |
| [`bank_manager_agent_control/`](bank_manager_agent_control/) | Multi-variant agent-control evaluation. |
| [`benchmark/`](benchmark/) | Benchmark configuration and scripts. |
| [`sandbox_action_mediation/`](sandbox_action_mediation/) | Stock Docker sandbox with pass/mock/block policy, deny-by-default audited egress, and judge-visible action evidence. |
| [`behavior_specs/`](behavior_specs/) | Reusable behavior specifications. |
| [`agents/`](agents/) | Shared tool modules, schemas, and connector fixtures used by other examples. |

For any non-trivial agent, prefer `target.callable` with `target.trace`. Use a
plain callable without traces only for a black-box API or a quick pipeline smoke
test.
