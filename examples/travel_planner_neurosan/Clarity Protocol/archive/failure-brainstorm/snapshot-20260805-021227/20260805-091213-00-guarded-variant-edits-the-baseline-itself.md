# Guarded variant edits the baseline itself

**Source:** mcp

The guarded variant patches the hardcoded validate_budget arguments or the shared advisory payload instead of gating the output. It measures a different system than the baseline, and a change to phoenix_auto_trace/_tools.py propagates to every other demo that imports it.
