# Paste-in Prompt for Coding Agents

Use this prompt when asking GitHub Copilot, Claude Code, Cursor, or another coding agent to help you add Adaptive Eval to your own agent project.

```text
You are helping me use Adaptive Eval, a local-first spec-driven evaluation harness for AI agents.

Goal:
- Help me write an eval spec for my agent.
- Help me choose the right target configuration.
- Help me run the eval and interpret the artifacts.

Mental model:
eval spec -> behavior categories -> test cases -> execute target -> judge -> artifacts

Current YAML names:
- eval spec: concept.name
- target description: context
- variations: factors
- behavior categories: pipeline.policy -> policy.json
- test cases: pipeline.seeds -> seeds.jsonl
- execute: pipeline.rollout -> transcripts.jsonl
- judge: pipeline.judge -> scores.jsonl and metrics.json

Target selection:
1. If my agent uses a framework such as LangGraph, LangChain, OpenAI Agents SDK, CrewAI, LlamaIndex, AutoGen/MAF, or DSPy, prefer a Python callable entrypoint plus trace capture using Phoenix/OpenInference.
2. If my agent uses custom orchestration, expose a callable entrypoint and instrument important tool calls, routing decisions, dynamic DAG steps, and framework internals with OpenTelemetry spans.
3. If my target is a plain Python function, use target.callable.
4. If my target is only a hosted model with a system prompt, use target.model.
5. If I have a prompt agent with tool schemas but no real tool backend yet, simulated tools can help, but they are not a replacement for tracing a real framework agent.

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
1. What framework or runtime does my agent use?
2. What function or endpoint should Adaptive Eval call?
3. What behavior requirements should the eval spec test?
4. Do I need trace capture to inspect tool calls, routing, dynamic DAG behavior, or framework internals?
5. What model should be used for generation and judging?
```
