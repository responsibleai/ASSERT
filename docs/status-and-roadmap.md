# Status and Roadmap

Adaptive Eval is in customer preview. It is useful for design partners and structured trials, but it is not a GA service.

## Stable enough to try

- `p2m init` — conversational config designer that creates eval configs interactively.
- Spec-driven pipeline: spec -> behavior categories -> test cases -> execute -> judge.
- Local artifact layout under `artifacts/results/`.
- `target.callable` for any agent or multi-agent system with a Python entrypoint.
- OTel/Phoenix trace capture for 33+ supported frameworks via two-line auto-instrumentation, plus custom OTel SDK spans for everything else — the recommended integration path.
- Hosted model targets.
- Prompt Agent target (`target.model` + `target.system_prompt` + optional `target.tools`) — declare agent behavior in YAML; the runtime owns the tool-call loop.
- Judge dimensions with descriptions and rubrics.

## Still evolving

- Public terminology and YAML aliases.
- SDK surface.
- Hosted/cloud workflow.
- Framework-specific quickstarts.
- Viewer UX.
- CI gating conventions.

## Preview expectations

You should expect:

- Some names in YAML to differ from the friendlier docs terminology.
- Examples to be the most reliable path.
- Local-first operation with explicit environment variables.
- JSON/JSONL artifacts rather than a hosted results service.

You should not expect:

- Production SLAs.
- A stable public SDK.
- A managed hosted service.
- Full per-framework parity in trace fidelity.

## Feedback we want

- Which target type matches your agent?
- Which framework do you need a quickstart for?
- Did the generated test cases match your spec?
- Did the judge verdict cite useful evidence?
- Were traces useful enough to debug failures?
- Which YAML names or docs sections were confusing?
