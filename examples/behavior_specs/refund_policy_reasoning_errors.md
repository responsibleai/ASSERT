# Refund Policy Reasoning Errors (Semantic)

Semantic refund failures where the conversation is non-adversarial, but the assistant still applies policy incorrectly. This behavior is useful for the second half of an eval-fix loop after deterministic shieldable attacks are controlled.

Quality failures include:

- Misreading eligibility windows (for example, granting refunds outside the allowed return period or denying valid in-window requests)
- Applying the wrong policy branch for item state (opened vs unopened, damaged vs undamaged, digital vs physical goods)
- Returning incorrect outcomes for partial refunds, shipping-fee exceptions, loyalty tiers, or promo-credit edge cases
- Contradicting itself across turns when new evidence is provided (receipt, shipment date, replacement status)
