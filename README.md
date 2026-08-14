<h1 align="center">
        <img src="https://raw.githubusercontent.com/responsibleai/ASSERT/main/assets/assert-logo.png" alt="ASSERT logo" width="22" style="vertical-align: middle; margin-right: 5px;"/>
        <span style="vertical-align: middle; font-family: 'Spline Sans Mono', monospace;">ASSERT.</span>
</h1>
<p align="center">
        Adaptive Spec-driven Scoring for Evaluation and Regression Testing<br/>
        Local-first. Framework-agnostic. Trace-aware.
</p>
<p align="center">
        <a href="https://github.com/responsibleai/ASSERT/blob/main/docs/getting-started.md">🚀 Get started</a> |
        <a href="https://responsibleai.github.io/ASSERT/">🌐 Visit project website</a> |
        <a href="https://github.com/responsibleai/ASSERT/blob/main/docs/targets/callable.md">🔌 View supported targets</a> |
        <a href="https://github.com/responsibleai/ASSERT/blob/main/docs/cli/overview.md">📘 CLI Reference</a> |
        <a href="https://github.com/responsibleai/ASSERT/blob/main/examples/README.md">🧪 Examples</a> |
        <a href="https://github.com/responsibleai/ASSERT/blob/main/assert_ai/library/behaviors/README.md">📋 Behavior Library</a>
</p>
<p align="center">
        <a href="https://github.com/responsibleai/ASSERT/actions/workflows/build.yml"><img src="https://github.com/responsibleai/ASSERT/actions/workflows/build.yml/badge.svg" alt="Build status"></a>
        <a href="https://www.python.org/downloads/" target="_blank"><img src="https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue.svg" alt="Python 3.11 | 3.12 | 3.13"></a>
        <a href="https://github.com/responsibleai/ASSERT/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-green.svg" alt="License"></a>
</p>
<p align="center">
        <img src="https://raw.githubusercontent.com/responsibleai/ASSERT/main/assets/assert-ai-framework-diagram.png" alt="Diagram of the ASSERT evaluation framework" width="100%">
</p>

## Why ASSERT?

Most AI systems start with a specification: product requirements, policies, system prompts, or launch criteria describing what the system should and should not do.

But evaluation often starts elsewhere: generic scorers, predefined benchmarks, or manual test cases that drift from the original intent.

ASSERT closes that gap. It turns your specified behaviors in natural language into structured, executable evaluations that can be reviewed, run, scored, and improved over time.

From the natural language specification, the ASSERT pipeline derives behavior categories, generates single-turn and multi-turn test cases, inferences them against your target, and uses an LLM judge to score each conversation against your policies.

## What you get with ASSERT

- **Spec-driven coverage** - test cases are generated from your product requirements and context, not a generic benchmark. You specify the behaviors that you want to test for
- **Curated behavior library** - a growing catalog of atomic, ready-to-use behavior presets ([`assert_ai/library/behaviors/`](assert_ai/library/behaviors/README.md)) spanning safety, bias/fairness, and agentic failure modes — the single source of truth for common behaviors, so you often don't have to write one from scratch. Pair with the [scenario library](assert_ai/library/scenarios/README.md) for ready-made application context.
- **Test any model endpoint** via integrations with [LiteLLM](https://github.com/BerriAI/litellm), supporting 100+ model endpoints from platform providers such as Bedrock, Azure, OpenAI, VertexAI, Cohere, Anthropic, Sagemaker, HuggingFace, VLLM, NVIDIA NIM.
- **Test any agent or multi-agent system** via integrations with [OpenInference](https://github.com/Arize-ai/openinference/). Evaluate a LangGraph agent, a CrewAI / OpenAI Agents SDK / DSPy / LlamaIndex / AutoGen system, custom multi-agent orchestration, a Python callable, or a hosted model — without rewriting the evaluation orchestration pipeline.
- **Agent trace-grounded judgment** - the recommended integration captures OpenTelemetry spans (OpenInference auto-instruments 33+ frameworks in two lines — `from assert_ai import auto_trace; auto_trace.enable()` — or you can emit your own with the OTel SDK) so the judge can cite tool calls, routing, model calls, and latency as evidence — not just the final response.
- **Test risky actions safely** - run a configured agent inside ASSERT's stock Docker sandbox, pass/mock/block its declared tool calls, deny direct internet access, and preserve attempted actions and audited proxy-aware egress as judge evidence. See the [sandboxed action-mediation example](examples/sandbox_action_mediation/README.md).
- **Portable artifacts** - every stage writes JSON/JSONL files locally for inspection, CI, and sharing.
- **Bundled local viewer** - browse runs side-by-side, pin a baseline, drill into per-behavior dimension breakdowns, and read judge justifications cited against the captured traces.

## Get started

ASSERT has two front doors:

- **[Guided — the `run-assert-eval` skill](#guided-the-run-assert-eval-skill)** *(recommended)* — describe your agent in chat. Your coding assistant discovers the risks with you, writes the eval configs, runs the pipeline, reports the failures, and can then generate a policy to fix them and prove the fix worked. No YAML by hand.
- **[Manual — the CLI](#manual-the-cli)** — write an `eval_config.yaml` yourself and run it.

### Guided: the `run-assert-eval` skill

The skill turns "I think my agent might do something bad" into measured evidence, and then into a deployable control. It chains three pieces:

| | | |
|---|---|---|
| **Clarity** | *discovery* | An interviewing agent that walks you through what your system is for and where it could fail, and writes the risks down. |
| **ASSERT** | *measurement* | Turns each risk into a generated test suite, runs it against your agent, and judges the transcripts. |
| **ACS** | *governance* | Generates an Agent Control Specification from the real failures, then re-runs the same eval against the governed agent to prove the rate dropped. |

Risks always come from Clarity — the skill won't let you seed an eval from an off-the-cuff description, because that is what produces low-signal results.

#### 1. Onboard (once per workspace)

You need **Python 3.12+** (ASSERT itself runs on 3.11+, but Clarity requires 3.12) and an IDE with MCP support — VS Code + Copilot agent mode, Claude Code, or Cursor. Clarity's discovery step runs as an MCP server, so this part can't be done from a bare terminal.

```bash
pip install -e ".[otel,langgraph]"   # install ASSERT
cp .env.example .env                 # add your provider key
assert-ai --help                     # verify

pip install -e ".[mcp]"              # from your clarity-agent checkout
clarity embed .                      # wires Clarity into this workspace
clarity doctor                       # verify an LLM provider is configured
```

Then **reload MCP servers** in your IDE and confirm the `run_clarity` tool is callable. `clarity embed .` generates `.vscode/mcp.json` and the `.clarity-protocol/` scaffold — `.vscode/mcp.json` contains an absolute path to *your* checkout, so it is gitignored and never committed.

Full checklist, including end-to-end verification: [`SETUP-CHECKLIST.md`](.claude/skills/run-assert-eval/SETUP-CHECKLIST.md).

#### 2. Explore what it produces

Six worked domains under [`examples/`](examples/README.md) show the complete
agent, one-behavior-per-YAML configs, setup, and results flow:

| Domain | Target shape |
|---|---|
| [`billing_support_agent`](examples/billing_support_agent/) | Python callable with tools — **the best one to read first** |
| [`travel_planner_langgraph`](examples/travel_planner_langgraph/) | LangGraph graph |
| [`travel_planner_neurosan`](examples/travel_planner_neurosan/) | Multi-agent network |
| [`azure_doc_qa`](examples/azure_doc_qa/) | Retrieval-grounded Q&A |
| [`change_control_agent`](examples/change_control_agent/) | Approval-workflow agent |
| [`science_research_agent`](examples/science_research_agent/) | Research agent |

The separate [`prompt_agents`](examples/prompt_agents/) directory is a compact
target-shape gallery, not another worked domain. Worked examples keep only the
runtime files, atomic eval configs, and README needed to understand and run
them; generated discovery and result artifacts stay uncommitted.

#### 3. Run an evaluation

Describe your agent in chat — what it does, what it can touch, and what it must never do:

> *Help me evaluate my billing support agent. Authenticated customers use it to check
> invoices, update payment methods, change plans, and request refunds up to $200. It can
> look up account/PII, issue refunds within policy, and escalate to a human. It must refuse
> legal/tax/financial advice, must not expose another customer's data, and must verify
> identity before high-risk actions (plan changes, cancellations, refunds).*

That description is the shipped [`billing_support_agent`](examples/billing_support_agent/) example. The more precisely you state the boundaries, the sharper the risks Clarity comes back with.

The skill then, with you in the loop:

1. **Discovers** risks via Clarity, or reuses an existing `.clarity-protocol/`.
2. **Stops at a triage gate** and shows you the candidate risks. You pick which to measure. Declining here writes nothing and runs nothing.
3. **Generates one atomic config per selected risk** — never one merged config, so each result is attributable to a single behavior.
4. **Confirms**, then runs the suites sequentially.
5. **Reports** the outcome with cited failing transcripts.

#### 4. Read the results

Results are reported as two separate headline metrics, and it matters that they stay separate:

- **Impermissible Behavior violated** — the agent violated a behavior the spec does **not** permit. This is the harm number.
- **Permissible Behavior violated** — the agent violated a behavior the spec **does** permit. This is the trade-off number.

A change that only moves the first one is a win; a change that drops the first by pushing up the second has mostly moved the problem. Every stage writes local artifacts under `artifacts/results/<suite>/<run>/`, so nothing is locked in a dashboard.

For anything visual — forest plots, comparing two runs, or stepping through a transcript with the judge's citations highlighted — use the bundled viewer:

```bash
cd viewer && npm install && npm run dev   # http://localhost:5174
```

#### 5. Govern the failure and prove the fix (ACS)

When a run surfaces real failures, ask the skill to fix and verify them. Rather than tweaking the prompt and hoping, it generates a deployable **ACS** policy from the actual findings and re-runs *the same eval* against the governed agent, so the improvement is measured rather than asserted:

```bash
assert-ai acs generate ...    # policy from the baseline findings
assert-ai acs validate ...    # check it against known-bad cases
```

The delta between the baseline and governed runs is the evidence. This requires a **callable** target whose risky tools can be wrapped — a hosted-model prompt agent has nothing to wrap. See [Securing agents with ACS](docs/guides/securing-agents-with-acs.md).

#### Where the skill lives

The same skill ships for three assistants, plus the workflows it follows:

| Path | Purpose |
|---|---|
| [`.claude/skills/run-assert-eval/`](.claude/skills/run-assert-eval/) | Claude Code — `SKILL.md` is the canonical definition |
| [`.github/prompts/run-assert-eval.prompt.md`](.github/prompts/run-assert-eval.prompt.md) | GitHub Copilot |
| [`.cursor/rules/assert.mdc`](.cursor/rules/assert.mdc) | Cursor |
| [`workflows/measure-clarity-failures.md`](.claude/skills/run-assert-eval/workflows/measure-clarity-failures.md) | Discovery → measurement loop |
| [`workflows/govern-and-remeasure.md`](.claude/skills/run-assert-eval/workflows/govern-and-remeasure.md) | ACS generation → governed re-run → delta |
| [`workflows/diagnose-acs-delta.md`](.claude/skills/run-assert-eval/workflows/diagnose-acs-delta.md) | What to do when the delta comes out wrong |

### Manual: the CLI

```bash
python -m pip install --upgrade pip      # requires pip >= 24.1
pip install -e ".[otel,langgraph]"       # install
cp .env.example .env                     # add your provider key
assert-ai run --config examples/travel_planner_langgraph/evals/budget_overrun.yaml
```

The pip upgrade is required on fresh devcontainers/base images: older pip
(< 24.1) crashes with `InvalidVersion: 'hosting'` while resolving one of the
Azure transitive dependencies' PEP 508 markers.

### Add a CI safety gate

Use [`responsibleai/assert-ai-action`](https://github.com/responsibleai/assert-ai-action) to run ASSERT as a PR regression gate — it fails the build when a change makes agent behavior significantly worse.

Install the skills into your coding agent (Cursor, Claude Code, Copilot, and [40+ others](https://github.com/vercel-labs/skills#supported-agents)). Two commands, because the bundle spans two repos on purpose — the evaluation skill is owned here in ASSERT and installed from here, so it never goes stale:

```bash
npx skills add responsibleai/ASSERT --skill run-assert-eval --yes
npx skills add responsibleai/assert-ai-action --skill wire-assert-ci --yes
```

Run them separately. `skills add` takes one package per invocation and silently ignores extras while still exiting 0, so a combined command looks like it worked and leaves you with half the bundle.

Then ask it to wire the gate:

> Use the `wire-assert-ci` skill to add an ASSERT safety gate to this repo.

No Node? Paste this instead — the agent fetches the skills itself:

```text
read https://raw.githubusercontent.com/responsibleai/assert-ai-action/main/ONBOARD.md
```

See [`docs/ci/`](docs/ci/README.md) for the short hand-off.

<table align="center" style="width: 100%; border: 1px solid #d0d7de; border-collapse: collapse;">
        <tr>
                <th style="border: 1px solid #d0d7de; padding: 10px; text-align: left;">🌐 Project website ↗</th>
                <th style="border: 1px solid #d0d7de; padding: 10px; text-align: left;">📝 Technical blog ↗</th>
                <th style="border: 1px solid #d0d7de; padding: 10px; text-align: left;">🚀 Quickstart guide ↗</th>
                <th style="border: 1px solid #d0d7de; padding: 10px; text-align: left;">📚 Documentation ↗</th>
        </tr>
        <tr>
                <td style="border: 1px solid #d0d7de; padding: 10px;"><a href="https://aka.ms/assert-ghpage">Learn about ASSERT</a></td>
                <td style="border: 1px solid #d0d7de; padding: 10px;"><a href="https://aka.ms/assert">Read the Command Line post</a></td>
                <td style="border: 1px solid #d0d7de; padding: 10px;"><a href="https://github.com/responsibleai/ASSERT/blob/main/docs/getting-started.md">Follow the full walkthrough</a></td>
                <td style="border: 1px solid #d0d7de; padding: 10px;"><a href="https://aka.ms/assert-docs">Browse concepts and guides</a></td>
        </tr>
</table>

## Acknowledgments

ASSERT's core method is **AI-assisted systematization** — turning a broad, contested behavior concept into an explicit, measurable specification — following **[Agarwal et al. (2026), *AI-Assisted Systematization for Evaluating GenAI Systems*](https://www.microsoft.com/en-us/research/publication/ai-assisted-systematization-for-evaluating-genai-systems/)** from Microsoft Research. The staged pipeline that turns that specification into generated scenarios, runs them against a target, and judges the results is modeled in spirit on the design of **[Bloom](https://github.com/safety-research/bloom)** and **[Petri](https://github.com/safety-research/petri)**, open-source behavioral-evaluation frameworks from the Anthropic alignment team (Safety Research, MIT licensed).

Adapted third-party material and the corresponding license notices are documented in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md). If you use ASSERT in research, please also cite Agarwal et al. (2026) and Bloom (see [`CITATION.cff`](CITATION.cff)).

### Team and contributors

ASSERT was built by the Microsoft Responsible AI organization.

- **Product:** Mehrnoosh Sameki, Minsoo Thigpen, Chang Liu, Abby Palia, Hanna Kim
- **Science:** Riccardo Fogliato, Emily Sheng, Alex Dow, Meera Chander, Alex Chouldechova, Sharman Tan, Xiawei Wang, Ahmed Magooda, Mayank Gupta, Jean Garcia-Gathright, Chad Atalla, Dan Vann, Hanna Wallach, Hannah Washington, Meredith Rodden, Nadine Frey, Melissa Kirkwood, Nick Pangakis, Ali Azad, Ahmed Elghory Ghoneim, Shushan Arakleyan
- **Engineering:** Mohamed Elmergawi, Jake Present, Aaron Aspinwall, Yeming Tang
- **Design:** Sooyeon Hwang, Becky Haruyama
- **Special thanks:** Roni Burd, Mohammad A, Heba Elfardy, Sandeep Atluri, Sydney Lister, Ram Shankar Siva Kumar, Andrew Gully

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft trademarks or logos is subject to and must follow [Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/en-us/legal/intellectualproperty/trademarks). Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship. Any use of third-party trademarks or logos is subject to those third party's policies.

## Telemetry

This project does not collect or send telemetry to Microsoft by default. Runs write local artifacts under `artifacts/results/`, and optional OpenTelemetry trace capture is controlled by your configuration and local collector setup, such as Phoenix.

If you configure a target, judge, trace collector, or model provider to send data to an external service, the prompts, responses, traces, metadata, and other evaluation artifacts sent to that service are governed by that service's terms and your configuration.

## Disclaimer: Risks and limitations of ASSERT

See the full section in the [`Concept Doc`](https://github.com/responsibleai/ASSERT/blob/main/docs/concepts.md#risks-and-limitations).
