# Retry loop exhausts six call budget

**Source:** mcp

A denial or an uninformative redaction marker prompts the model to search again with different phrasing. MAX_TOOL_CALLS is 6, so a few retries exhaust the budget and the turn ends with no answer on a legitimately answerable question.
