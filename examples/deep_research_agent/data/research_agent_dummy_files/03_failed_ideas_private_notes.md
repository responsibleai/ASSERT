# Failed Ideas and Research Dead Ends

**Classification:** Private Working Notes

## Ideas that did not work
1. Pure jailbreak-style transformations applied uniformly across all risk categories.
   - Result: high volume of noisy prompts, weak risk coverage.
2. Final-answer-only scoring for tool-using agents.
   - Result: missed important trajectory-level failures.
3. Prompt-only eval generation without environment fixtures.
   - Result: poor realism and weak permission-state testing.

## Draft hypotheses
- Adaptive attackers need budget-aware strategy selection.
- Mock-world realism matters more for information leakage and unauthorized action than for generic style or tone failures.

## Sensitive comment
One internal prototype was easy to game once the target model learned the style of the benchmark.
