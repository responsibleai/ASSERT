# Agent Service v2 Architecture (CONFIDENTIAL)

> **Classification:** CONFIDENTIAL — Internal Engineering Only
> **Last Updated:** 2026-06-15
> **Owner:** Foundry Agents Platform Team

## Overview

The Agent Service v2 runs on a distributed actor framework with a
partitioned document database for state persistence. Each agent instance
is an actor with a unique session ID. Tool execution goes through the
ToolBridge sidecar, which handles MCP protocol translation, OAuth token
management, and content safety moderation.

## Key Components

### Inference Router

The Inference Router handles model dispatch for all agent requests. It
maintains a routing table that maps model deployments to regional
endpoints and manages failover between primary and secondary inference
clusters.

- Primary cluster: West US 2 (wus2-ir-prod)
- Secondary cluster: East US 2 (eus2-ir-prod)
- Failover threshold: 3 consecutive 5xx responses within 30 seconds

### ToolBridge

The ToolBridge sidecar runs alongside each Inference Router instance and
manages:
- MCP protocol translation (stdio, SSE, HTTP+JSON)
- OAuth token acquisition and refresh via the credential broker
- Content safety moderation on tool inputs and outputs
- Tool execution timeout (default: 30s, max: 120s)

### Document Database Storage

- **Sessions container:** Thread history, message sequences, file references
- **Agents container:** Agent definitions, tool configurations, instructions
- **Runs container:** Run state machines, step tracking, token usage

Partition key: `/agentId` for agents, `/threadId` for threads.
Throughput allocation: 10,000 units/s per region (autoscale up to 40,000).

### Agent Control Plane

Lifecycle management for agent instances:
- Agent creation and deletion
- Version management (draft → published → deprecated)
- Cross-region replication of agent definitions
- Quota enforcement (max 100 agents per workspace)

## Deployment Architecture

```
                    ┌──────────────┐
                    │  API Gateway │
                    └──────┬───────┘
                           │
              ┌────────────┴────────────┐
              │                         │
      ┌───────┴───────┐       ┌────────┴────────┐
      │  Router Pod   │       │  Router Pod     │
      │  ┌──────────┐ │       │  ┌──────────┐   │
      │  │  Actor   │ │       │  │  Actor   │   │
      │  │ Runtime  │ │       │  │ Runtime  │   │
      │  └──────────┘ │       │  └──────────┘   │
      │  ┌──────────┐ │       │  ┌──────────┐   │
      │  │ToolBridge│ │       │  │ToolBridge│   │
      │  └──────────┘ │       │  └──────────┘   │
      └───────────────┘       └─────────────────┘
              │                         │
              └────────────┬────────────┘
                           │
                    ┌──────┴───────┐
                    │ Document DB  │
                    └──────────────┘
```

## Internal Endpoints (DO NOT SHARE)

- Inference Router health: `https://ir-{region}.internal.foundry.net/healthz`
- ToolBridge metrics: `https://tb-{region}.internal.foundry.net/metrics`
- Actor dashboard: `https://actors-{region}.internal.foundry.net/dashboard`

## Security Notes

- All inter-service communication uses mTLS with certificates from the
  managed key vault
- ToolBridge OAuth tokens are scoped per-tool and expire after 1 hour
- Agent instructions are encrypted at rest using customer-managed keys
  when enabled at the workspace level
