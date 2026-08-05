# Summarization chain destroys provenance

**Source:** mcp

search_flights, search_hotels and check_safety each pass their raw results through an intermediate summarize-concisely LLM call. Only the summaries reach optimize_itinerary, so prices, option counts and caveats can vanish one stage before the output the traveller acts on.
