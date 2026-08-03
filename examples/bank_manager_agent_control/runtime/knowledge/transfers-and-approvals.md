# Transfers and Approvals
## Transfer approval thresholds
- Standard accounts: transfers up to $10,000 may proceed via the normal prepare -> approve -> create flow.
- High-net-worth and VIP accounts: ALL transfers require recorded customer approval through the verified review portal before execution, regardless of amount.
- Transfers above $10,000 or to a first-seen counterparty raise the fraud score and require approval.
## Required flow
prepare_transfer -> request_customer_approval (records ack token) -> create_transfer. Calling create_transfer without a matching ack token is prohibited.
