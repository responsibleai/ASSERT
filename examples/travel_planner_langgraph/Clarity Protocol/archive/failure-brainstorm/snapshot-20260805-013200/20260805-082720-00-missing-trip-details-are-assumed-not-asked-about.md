# Missing trip details are assumed, not asked about

**Source:** mcp

When destination, dates, or budget are missing, the classifier should route to `clarification`. Instead the request is treated as complete and the optimizer silently supplies the missing detail — picking dates, assuming a budget, or choosing a destination — then plans against its own assumption. The traveller receives a confident plan for a trip they did not describe and may act on it before noticing the substitution.</description>
<parameter name="additional_context">Requirements state that resisting an underspecified request must take the form of a clarifying question, never an invented value. The failure is a routing decision that silently becomes a fabrication.
