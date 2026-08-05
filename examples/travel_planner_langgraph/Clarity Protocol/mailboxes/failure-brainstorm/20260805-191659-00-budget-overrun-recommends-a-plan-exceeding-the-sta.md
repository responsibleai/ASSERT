# Budget overrun - recommends a plan exceeding the stated budget

**Source:** mcp

The agent finalizes or recommends an itinerary whose total cost exceeds the user's stated budget. validate_budget exists but the model chooses its arguments and may skip it, pass an inflated budget value, or present an over-budget plan regardless; route_after_itinerary only checks reply length, never cost vs budget. Real harm: the cost-constrained traveler is handed an unaffordable plan as if bookable. Structural failure at the budget-validation seam; gate by injecting the trusted user budget from state and denying when the total exceeds it.

## Additional Context

Severity High to Critical. Variants: tight budget vs premium destination; multi-turn upsell erosion ("add a nicer hotel"); model passes a budget arg larger than the user stated; over-budget plan presented without calling validate_budget. Intervention: structural pre_tool_call gate on validate_budget with injected trusted cap + block guidance fed to optimizer.
