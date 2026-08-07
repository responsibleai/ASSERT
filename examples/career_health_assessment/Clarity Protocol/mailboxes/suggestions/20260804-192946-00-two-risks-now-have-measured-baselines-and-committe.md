# Two risks now have measured baselines and committed ACS governance

**Source:** mcp
**Target:** failures/failures.md

Mark failure-01 (fabricated/unsupported inference) and failure-02 (CV-embedded prompt injection) as MEASURED + GOVERNED. Baselines and governed A/B live at examples/career_health_assessment/evals/<risk>/ with committed output-annotator gates at examples/career_health_assessment/acs/<risk>/. Deltas (harm = non-permissible policy violation, prompt/scenario): fabrication 81.0->46.2% / 28.6->21.4%; injection 42.1->0.0% / 25.0->0.0% (eliminated). Note the annotator must run on azure/gpt-5.4 (same as the judge) or the gate under-fires. Residual fabrication harm is subtle evaluative narrative language -> follow-up: stronger regen or a paired-CV bias eval (failure-04) next.

## Rationale

Close the Clarity loop so staleness tracking knows these two failure modes now have a measured, governed baseline and where the evals live.
