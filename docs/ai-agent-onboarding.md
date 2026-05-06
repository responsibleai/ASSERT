# Paste-in Prompt for Coding Agents

Use this prompt when asking GitHub Copilot, Claude Code, Cursor, or another coding agent to help you add Adaptive Eval to your own agent project.

```text
You are helping me use Adaptive Eval, a local-first spec-driven evaluation harness for AI agents.

Goal:
- Help me write an eval spec for my agent.
- Help me choose the right target configuration.
- Help me run the eval and interpret the artifacts.

Mental model:
eval spec -> failure_mode categories -> test cases -> execute target -> judge -> artifacts

Current YAML names:
- eval spec: spec.name
- target description: context
- variations: factors
- failure_mode categories: pipeline.taxonomy -> taxonomy.json
- test cases: pipeline.seeds -> seeds.jsonl
- execute: pipeline.inference -> transcripts.jsonl
- judge: pipeline.judge -> scores.jsonl and metrics.json

Target selection:
1. If my agent or multi-agent system has any Python entry function (frameworks such as LangGraph, LangChain, OpenAI Agents SDK, CrewAI, LlamaIndex, AutoGen/MAF, DSPy — or custom orchestration with no framework), use target.callable. Trace capture using Phoenix/OpenInference is an optional upgrade for richer judge evidence.
2. If my target is a plain Python function that wraps a hosted model, use target.callable.
3. If my target is only a hosted model with a system prompt, use target.model.
4. If I have a prompt agent with tool schemas but no real tool backend yet, simulated tools can help, but they are not a replacement for evaluating a real agent or multi-agent system.

I do not need to understand OpenTelemetry to start. Trace capture is opt-in.

Setup preference:
- Prefer pip install for preview setup, especially on macOS:
  python -m venv .venv
  source .venv/bin/activate
  python -m pip install --upgrade pip
  python -m pip install -e ".[otel,langgraph]"

Security:
- Never read, print, commit, or infer values from .env or other local environment files.
- Use placeholder environment variable names such as AZURE_API_KEY and AZURE_API_BASE.
- Do not commit generated artifacts, traces, logs, or virtual environments.

Ask me these questions first:
1. What runtime does my agent or multi-agent system use? (framework, custom orchestration, or plain hosted model)
2. What function or endpoint should Adaptive Eval call?
3. What failure_mode requirements should the eval spec test?
4. Optional: do I want trace capture so the judge can inspect tool calls, routing, dynamic DAG failure_mode, or framework internals? (Skippable on the first run.)
5. What model should be used for generation and judging?
```
