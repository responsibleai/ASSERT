# Incorrect Tool Selection Failures

Incorrect tool selection failures occur when an agent has the right
tool available but reaches for a different one, or invents a tool
use that doesn't fit the step at hand. The chosen tool may
superficially relate to the user's request, but it cannot produce the
information or effect the step actually requires. These failures are
distinct from sequencing or argument errors — the tool itself is the
wrong choice. Quality failures include:

- Picking a tool whose name partially matches the user's keywords
  rather than the tool whose function fits the step
- Reaching for a generic search tool when a specialized lookup tool
  is documented and available
- Calling a read-only tool when a write/action tool is required (or
  vice versa)
- Using a tool outside its documented scope (e.g., calling a
  weather-lookup tool to get traffic data)
- Skipping an available tool and answering from the model's prior
  knowledge when fresh, authoritative data was required
- Trying to call a tool that does not exist in the provided
  toolset, instead of selecting from the actual list
- Selecting a tool whose preconditions are not met (e.g., calling a
  "send email" tool before having a recipient address)
