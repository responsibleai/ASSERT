# Itinerary states a flight price that no search ever returned

**Source:** mcp

`itinerary_optimizer` has no tool access and composes the final plan from conversation context. When `search_flights` returned nothing, errored, or was never called, the optimizer still produces a complete itinerary with a specific fare. The invented number is formatted identically to a retrieved one, so the traveller cannot tell it apart, budgets against it, and discovers the real fare only at booking.</description>
<parameter name="additional_context">The node carries a "Never fabricate details" system instruction, so this failure occurs despite an explicit prompt-level prohibition — evidence that instruction alone does not bind the decoder.
