# Incident Response Playbook (INTERNAL)

> **Classification:** INTERNAL — On-Call DRI Only
> **Last Updated:** 2026-06-01
> **Owner:** Agents Livesite Team

## Severity Definitions

| Severity | Impact | Response Time | Escalation |
|----------|--------|---------------|------------|
| Sev 0 | Complete service outage | 5 min | VP-level bridge |
| Sev 1 | >10% customers affected | 15 min | GM-level bridge |
| Sev 2 | Single region degraded | 30 min | Team lead |
| Sev 3 | Non-customer-impacting | Next business day | Sprint backlog |

## On-Call Rotation

- Primary DRI: Rotates weekly, Monday 9am PT to Monday 9am PT
- Secondary DRI: Previous week's primary
- Escalation chain: DRI → Team Lead → Engineering Manager → VP

## Incident Response Steps

### Step 1: Acknowledge

Within the SLA for the incident severity:
- Join the incident bridge call
- Post acknowledgment in the Teams on-call channel
- Set incident status to "Investigating"

### Step 2: Triage

1. Check service health dashboards for anomalies
2. Query telemetry for error rates: `AgentRequests | where statusCode >= 500`
3. Check recent deployments: any rollout in the last 4 hours?
4. Check dependency health: Document DB, Inference Router, ToolBridge, API Gateway

### Step 3: Mitigate

Common mitigation actions:
- **Bad deployment:** Initiate rollback via the safe-deployment portal
- **Database throttling:** Increase throughput allocation via management API
- **Inference Router overload:** Scale out actor runtime pod count
- **ToolBridge timeout:** Increase tool timeout or disable problematic tool

### Step 4: Resolve

- Verify metrics return to baseline
- Update the incident record with root cause and mitigation
- Schedule postmortem within 5 business days for Sev 0/1

## Key Dashboards

- Agent Service Health: `https://monitoring.internal.foundry.net/dashboard/AgentHealth`
- Inference Latency: `https://monitoring.internal.foundry.net/dashboard/InferenceLatency`
- ToolBridge Errors: `https://monitoring.internal.foundry.net/dashboard/ToolBridgeErrors`
- Database Throughput: `https://monitoring.internal.foundry.net/dashboard/DatabaseThroughput`

## Escalation Contacts

| Role | Alias | Phone |
|------|-------|-------|
| Agents Team Lead | agents-lead@contoso.com | 555-0101 |
| Inference Router Lead | ir-lead@contoso.com | 555-0102 |
| Database DRI | db-dri@contoso.com | 555-0103 |
| API Gateway DRI | gw-dri@contoso.com | 555-0104 |

## Telemetry Quick Reference

```sql
-- Error rate by region (last 1 hour)
AgentRequests
| where timestamp > ago(1h)
| summarize total=count(), errors=countif(statusCode >= 500) by region
| extend error_rate = round(100.0 * errors / total, 2)
| order by error_rate desc

-- Slow requests (p99 latency)
AgentRequests
| where timestamp > ago(1h)
| summarize p99=percentile(durationMs, 99) by bin(timestamp, 5m), region
| render timechart

-- Tool execution failures
ToolBridgeLogs
| where timestamp > ago(1h) and level == "Error"
| summarize count() by toolName, errorType
| order by count_ desc
```
