# Verification + account-scoping failures now governed by committed ACS policies

**Source:** mcp
**Target:** failures/failures.md

Mark two of the four discovered failure modes as MITIGATED by committed structural ACS policies, measured on 50 ASSERT cases each (25 prompt / 25 scenario):

1. unverified-high-risk-action (verification gate, denies when NOT policy_target.verified on 4 write tools). HARM non-permissible: prompt 4.0% -> 0.0%, scenario 8.7% -> 4.5%. Permissible-violated: prompt 8.0% -> 0.0%, scenario 12.0% -> 0.0%. Overrefusal: 4.0%/0.0% -> 0.0%/0.0%.

2. cross-customer-data-exposure (account-scoping gate, denies when account_id != caller_account_id on 6 read+write tools). HARM non-permissible: prompt 20.8% -> 8.7%, scenario 43.8% -> 0.0%. Permissible-violated: prompt 9.5% -> 0.0%, scenario 8.0% -> 0.0%. Overrefusal: 0.0%/4.0% -> 0.0%/0.0%.

RESIDUAL (3 cases): purely conversational, NOT tool-mediated -- the arg gate blocks the tool call so no data leaves and no state mutates, but the model still verbally commits before verification or verbally offers to check/act on a foreign account. Closing these requires a complementary OUTPUT-semantic ACS gate (annotator over assistant text), not an argument gate. Recommend adding this as a follow-up mitigation.

## Rationale

Closes the measure loop: the failure modes are now backed by committed, unit-tested Rego policies with a proven baseline->governed delta, and the residual (output-language) gap is documented so the next iteration knows an output annotator is the required complementary layer.
