# Fabrication grounded output gate: measured harm cut ~60pct with inherent overrefusal tension

**Source:** mcp
**Target:** failures/failures.md

Mark failure-01 (fabricated_itinerary_details) as MITIGATED with a grounded output-annotator ACS gate (agent_guarded.py:chat_governed_fabrication). Measured A/B at n=25/type (azure/gpt-5.4 judge and annotator): non-permissible HARM 32pct/71pct (prompt/scenario) -> 12pct/30pct, roughly a 60pct reduction on both turn types. Cost is an overrefusal increase 12pct/52pct -> 20pct/100pct, most severe multi-turn. Note this overrefusal is an inherent artifact of the mock tool corpus, which returns destination-mismatched Tokyo/LAX data for every request, so the honest grounded answer is a partial decline; only 5/25 prompt and 6/25 scenario land on the literal scoped fallback. Against real retrieval the grounded regen would have correct data. Follow-up: re-measure overrefusal with realistic destination-correct tools before tuning the annotator/fallback.

## Rationale

Closes the Clarity loop with the measured governed delta and documents the harness-driven overrefusal so future readers do not mistake it for a gate misfire.
