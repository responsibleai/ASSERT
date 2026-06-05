# Agent Run Log

This file records every observation-loop pass the autonomous-agent system performs while VACATION MODE is active. One line per loop completion.

**Format:** `[timestamp UTC] [agent] [items observed] [items logged] [anomalies]`

VACATION MODE is the default. Entries here describe what each agent observed and any narrow vacation-mode writes (audit comments, co-owner pings) it issued under the rules in [AGENTS.md `Default state: VACATION MODE` section](../../../AGENTS.md).

## Run log

| timestamp UTC | agent | items observed | items logged | anomalies |
|---|---|---|---|---|
| <timestamp> | <agent> | <count> | <count> | <notes> |