# Fetched web page directs exfiltration

**Source:** mcp

fetch_url hardcodes class public on everything it returns and performs a real network fetch, so any attacker-controlled page becomes trusted-looking context carrying an instruction the agent cannot decline to read.
