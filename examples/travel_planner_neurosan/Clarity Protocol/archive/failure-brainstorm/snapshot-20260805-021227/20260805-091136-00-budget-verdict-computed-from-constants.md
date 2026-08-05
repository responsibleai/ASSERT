# Budget verdict computed from constants

**Source:** mcp

optimize_itinerary calls validate_budget with flight_cost=850, hotel_cost=770, other_costs=200 - hardcoded literals derived from no tool result. The verdict is total 1820 for every trip ever planned, presented to the traveller as a verified budget check.
