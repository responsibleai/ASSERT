# Trip duration ignored in budget total

**Source:** mcp

hotel_cost=770 is seven nights at the cheapest rate. The days value extracted by classify_intent is never used, so a fourteen-day request validates against seven nights and the traveller under-budgets by half the accommodation.
