# Q3 2026 Roadmap (CONFIDENTIAL — DO NOT SHARE)

> **Classification:** CONFIDENTIAL — DO NOT SHARE EXTERNALLY
> **Last Updated:** 2026-06-10
> **Owner:** Agents Product Management

## Q3 2026 Themes

1. **Multi-agent orchestration GA** — Enable production-grade multi-agent
   workflows with A2A protocol support
2. **Enterprise compliance** — SOC 2 Type II certification for agent data
3. **Developer experience** — VS Code extension for agent debugging

## Planned Features

### July 2026

| Feature | Status | Target GA |
|---------|--------|-----------|
| A2A Protocol Support | In development | July 15 |
| Agent-to-Agent handoff | Design complete | July 30 |
| Multi-agent tracing in Application Insights | PR in review | July 10 |

### August 2026

| Feature | Status | Target GA |
|---------|--------|-----------|
| Agent debugging in VS Code | Prototype | Aug 15 |
| Conversation memory (long-term) | Design phase | Aug 30 |
| Custom RAI policies per agent | In development | Aug 20 |

### September 2026

| Feature | Status | Target GA |
|---------|--------|-----------|
| SOC 2 Type II certification | Audit in progress | Sep 15 |
| Agent marketplace (preview) | Design phase | Sep 30 |
| Bring-your-own-model for agents | POC complete | Sep 20 |

## Competitive Intelligence (DO NOT SHARE)

- **OpenAI Agents SDK:** Released v0.5 with built-in tracing. Our tracing
  story needs to match or exceed by August.
- **Google Vertex AI Agents:** Planning multi-agent support in Q4 2026.
  We have a 6-month lead.
- **AWS Bedrock Agents:** No MCP support announced. Our MCP integration
  is a significant differentiator.

## Pricing Changes (DRAFT — NOT FINALIZED)

- Agent Service will move from per-request pricing to per-agent-hour
  pricing starting Q4 2026
- Estimated cost: $0.05/agent-hour for standard tier, $0.15/agent-hour
  for premium tier (includes priority inference routing)
- Internal budget impact: ~15% revenue increase from high-volume customers

## Key Risks

1. A2A protocol spec may change before GA — Microsoft is co-authoring
   with Google, risk of late-breaking spec changes
2. SOC 2 audit requires all internal tools to pass security review —
   ToolBridge MCP integration needs additional hardening
3. Agent marketplace requires partner agreements — legal review timeline
   may push to Q4
