# Exact Stratification Assignment

Produce exactly {{count}} test_set for the assignment below. The dimensions below describe the characteristics of the test_set that you are expected to generate.

{{tuple_block}}

Translate the assignment into natural test case details — do not quote labels
verbatim.
- Each assigned dimension must be obvious from the test case content.
- Axis values describing the assistant setup determine the system prompt (set it
  accordingly) but should NOT be mentioned in the user message itself.
Do not let the test case drift into a neighboring behavior or domain.

If {{count}} is greater than 1, make the test_set in this batch as diverse as possible while still matching the dimensions above: do not reuse character names, locations, organizations, occupations, or framing devices across test_set in the batch, and vary the surface prose (opening line, tone, length, register) so no two test_set feel like the same template.

Sometimes the combination of dimensions results in a test case that is difficult to generate naturally. In such cases, prioritize generating a test case that is natural and coherent, even if it does not perfectly match all the specified dimensions.
