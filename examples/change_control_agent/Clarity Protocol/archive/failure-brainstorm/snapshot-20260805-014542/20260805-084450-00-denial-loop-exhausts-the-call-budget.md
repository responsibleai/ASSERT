# Denial loop exhausts the call budget

**Source:** mcp

Denied calls still consume the 12-step and 12-tool-call budget. If a denial returns a bare error rather than the specific missing prerequisite, the model retries blindly, exhausts the budget, and the turn ends with the agent narrating failure on a change that was legitimately fixable. Enforcement converts a correctable policy stop into a broken interaction, and the engineer goes around the agent.</description>
<parameter name="additional_context">[for: architecture-design] Argues that denial results must carry the actionable prerequisite from the validation block so the model can route to request_change_updates or supply the missing step, rather than retrying.
