# Stated budget silently replaced by default

**Source:** mcp

_as_number substitutes 7 days and 3000 budget for any value it cannot coerce, including JSON null. A traveller who stated a 1200 budget can silently have it replaced by 3000, after which the budget verdict is meaningless.
