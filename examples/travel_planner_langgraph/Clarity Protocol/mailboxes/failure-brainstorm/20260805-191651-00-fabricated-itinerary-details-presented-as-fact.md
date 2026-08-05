# Fabricated itinerary details presented as fact

**Source:** mcp

The itinerary_optimizer node invents concrete specifics the tools never returned — flight numbers, exact departure/arrival times, prices, hotel names, confirmation codes, visa terms — and presents them with confident specificity. The only guardrail is a single system-prompt line. Pressure is amplified because the mock tools return generic, destination-mismatched data, so the model fills gaps to look complete. Real harm: a user books against details that do not exist. Semantic failure in the reply text; gate at the output point with an LLM annotator.

## Additional Context

Severity Critical. Variants: destination with no matching tool data; multi-turn "just give me the final numbers" pressure; request for a confirmation code / exact flight time; thin tool result padded with plausible detail. Intervention: output annotator gate + regenerate-and-re-gate.
