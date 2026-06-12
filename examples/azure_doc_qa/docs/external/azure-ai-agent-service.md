# Azure AI Agent Service Overview

Azure AI Agent Service enables developers to build, deploy, and scale
AI agents that can reason over data, take actions, and collaborate with
other agents. It is part of Azure AI Foundry and supports the
OpenAI-compatible Agents API.

## Key Features

- **Tool use:** Agents can call functions, search files, execute code,
  and interact with external services via MCP (Model Context Protocol).
- **Multi-agent workflows:** Multiple agents can collaborate on complex
  tasks using the Agent-to-Agent (A2A) protocol.
- **Managed infrastructure:** No need to manage compute, storage, or
  networking — Azure handles scaling automatically.
- **Enterprise security:** Built-in content moderation, data encryption,
  and Azure RBAC integration.

## Getting Started

### Prerequisites

- An Azure subscription
- An Azure AI Foundry workspace
- Python 3.10+ with the Azure AI Projects SDK

### Create Your First Agent

```python
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

client = AIProjectClient(
    credential=DefaultAzureCredential(),
    endpoint="https://<your-workspace>.services.ai.azure.com"
)

agent = client.agents.create_agent(
    model="gpt-5",
    name="my-first-agent",
    instructions="You are a helpful assistant."
)
```

### Run a Conversation

```python
thread = client.agents.create_thread()
client.agents.create_message(
    thread_id=thread.id,
    role="user",
    content="What is Azure AI Foundry?"
)
run = client.agents.create_run(
    thread_id=thread.id,
    agent_id=agent.id
)
```

## Supported Models

- GPT-5 and GPT-5 mini
- GPT-4o and GPT-4o mini
- o3 and o3-mini
- Custom fine-tuned models deployed to Azure AI Foundry

## Pricing

Agent Service is billed per API call plus inference token usage. See
the Azure pricing calculator for current rates.

## Known Limitations

- Maximum 100 agents per workspace
- Maximum 10,000 messages per thread
- File uploads limited to 512MB per file
- Tool execution timeout: 120 seconds maximum
- File search results may take up to 5 minutes to reflect index updates
  (fix planned for v2.4)

## Related Documentation

- [Azure AI Foundry documentation](https://learn.microsoft.com/azure/ai-studio/)
- [Agents API reference](https://learn.microsoft.com/azure/ai-services/agents/reference)
- [MCP integration guide](https://learn.microsoft.com/azure/ai-services/agents/mcp)
