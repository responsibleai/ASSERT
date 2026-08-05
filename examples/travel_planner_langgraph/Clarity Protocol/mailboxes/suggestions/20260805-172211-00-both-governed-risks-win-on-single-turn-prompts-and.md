# Both governed risks win on single-turn prompts and lose on multi-turn scenarios; grounding, not classification, is the binding constraint

**Source:** mcp
**Target:** failures.md

An output-side gate on this planner reliably removes fabricated figures on single-turn prompts and reliably fails on multi-turn scenarios, and the reason is that the gate is being asked to compensate for missing grounding rather than to classify text.

Prompt split, shipped governed run. Costs: harm 16/23 to 7/21. Entry: harm stays at 2 flagged rows while permissible violations fall 11/23 to 8/25 and over-refusal falls 44% to 32%, so the gate suppressed invention without suppressing legitimate answering. The strongest measured costs configuration cut harm 16/23 to 3/21 with permissible flat at 2 to 3 rows and over-refusal exactly flat at 0/25.

Scenario split, same policies. Costs permissible violations rise 12/23 to 17/24 and over-refusal 48% to 75%. Entry rises 11/19 to 16/18 and 50% to 89.5%. Every one of the four attempts shows this shape, including attempts that softened the annotator, and softening measurably re-opened harm (costs prompt harm returned to 47.4%, entry scenario harm to 81.8%).

The mechanism is architectural, not a tuning failure. Scenario conversations frequently never reach the research step, so the retrieval record is empty. With no supporting evidence in hand the only correct action for an output gate is to decline, and it must decline again on every subsequent turn because nothing in the conversation ever supplies the missing evidence. Ten turns of correct refusals read to the judge as an unhelpful agent. The Clarity architecture forbids the enforcement wrapper from fetching the missing grounding itself, and rightly so, because a wrapper that retrieves is no longer a control.

The implication for the protocol is that these two risks should not be specified as pure output-classification risks. Both are grounding risks. The behavioural requirement that actually matters is that the planner must retrieve before it quotes a price or an entry requirement, which is a property of the agent's control flow, not of its final text. Consider respecifying them so the required behaviour is retrieval-before-assertion, which would put enforcement at the point where the agent is about to assert without a record, and would let the remedy be to retrieve rather than to decline.

One caveat is recorded honestly. The shipped wrapper's depth-based fallback rotation was inert during measurement because conversation history was not threaded into it, so every declining turn emitted identical wording. The prompt split is single-turn and therefore unaffected, so the prompt-split results stand. The scenario over-refusal magnitude cannot be cleanly attributed to the policy alone and should be treated as an upper bound on the cost, not a precise measurement of it.

## Rationale

Derived from a full Clarity to ASSERT to ACS to ASSERT cycle over both Critical risks (fabricated_cost_claim, unsupported_entry_requirement), baseline plus four governed attempts per risk, all sharing one systematize v0001 and one test_set v0001 so every arm is directly comparable. All rates were re-derived from raw flagged/applicable counts rather than read from summary rates, because the judge marks a node not-applicable when the transcript never engages it, so a rate can move opposite to the underlying count.
