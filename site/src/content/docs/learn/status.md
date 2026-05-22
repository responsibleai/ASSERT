---
title: Status & roadmap
description: What's in preview today, what's coming.
---

Adaptive Eval is in **preview**. The pipeline, CLI, viewer, and documented examples are usable today; the surface is being shaped with preview users before broader release.

## Working today

- The four-stage pipeline (taxonomy → test cases → execute → judge) on any LiteLLM-supported provider.
- `target.callable` integration for any Python entrypoint.
- OpenTelemetry auto-instrumentation via Phoenix / OpenInference (33+ frameworks).
- `target.model` + `target.tools` for hosted models with simple tool sets.
- The viewer (SvelteKit) for browsing artifacts.
- Three documented end-to-end examples (travel planner, health assistant, incident triage).

## In flight

- Float-scale rubrics (not just true / false).
- Span / trace isolation — score only specific event types within a transcript.
- Multi-callable / event-triggered routing — same agent with different entry points.
- Multimodality (file uploads, image generation).
- Per-framework instrumentation parity (some OpenInference instrumentors have known span gaps).

## Tracked in GitHub

Public issues live at [microsoft/adaptive-eval/issues](https://github.com/microsoft/adaptive-eval/issues). Active threads cover provider/model quirks, onboarding paths, PyPI distribution, CLI/viewer alignment, and adversarial-result interpretation.
