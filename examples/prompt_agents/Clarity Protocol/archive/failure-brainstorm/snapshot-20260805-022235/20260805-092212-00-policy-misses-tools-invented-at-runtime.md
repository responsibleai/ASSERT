# Policy misses tools invented at runtime

**Source:** mcp

A test case invents check_herbal_dosing or similar. A policy enumerating the four fixed tool names never fires. The tool surface in per_test_case mode is authored at runtime by a generator asked to serve a distrustful user, so any allowlist or denylist keyed on tool identity is structurally incomplete.
