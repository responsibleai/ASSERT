# Azure AI Foundry Quickstart

Get started with Azure AI Foundry in minutes. This guide walks you
through creating a project, deploying a model, and making your first
inference call.

## Prerequisites

- An Azure subscription (create a free account if you don't have one)
- Azure CLI installed and logged in

## Step 1: Create a Foundry Hub and Project

```bash
az ml workspace create \
  --name my-ai-hub \
  --resource-group my-rg \
  --kind hub \
  --location eastus2

az ml workspace create \
  --name my-ai-project \
  --resource-group my-rg \
  --kind project \
  --hub-id /subscriptions/<sub>/resourceGroups/my-rg/providers/Microsoft.MachineLearningServices/workspaces/my-ai-hub
```

## Step 2: Deploy a Model

Navigate to the Model Catalog in Azure AI Foundry portal and deploy a
model:

1. Go to https://ai.azure.com
2. Select your project
3. Navigate to **Model catalog** in the left menu
4. Search for "GPT-5" and click **Deploy**
5. Choose a deployment name and select the Standard SKU
6. Click **Deploy**

## Step 3: Make an Inference Call

```python
from azure.ai.inference import ChatCompletionsClient
from azure.identity import DefaultAzureCredential

client = ChatCompletionsClient(
    endpoint="https://<your-project>.services.ai.azure.com",
    credential=DefaultAzureCredential()
)

response = client.complete(
    model="gpt-5",
    messages=[
        {"role": "user", "content": "What is Azure AI Foundry?"}
    ]
)

print(response.choices[0].message.content)
```

## Step 4: Explore More Features

- **Prompt flow:** Build and test prompt chains visually
- **Evaluations:** Evaluate model quality with built-in metrics
- **Agents:** Create AI agents with tool use and memory
- **Fine-tuning:** Customize models with your own data

## Supported Regions

Azure AI Foundry is available in the following regions:
- East US, East US 2, West US, West US 2, West US 3
- North Central US, South Central US
- West Europe, North Europe, Sweden Central
- Japan East, Australia East, Southeast Asia

## Next Steps

- [Build an AI agent](https://learn.microsoft.com/azure/ai-services/agents/quickstart)
- [Set up evaluations](https://learn.microsoft.com/azure/ai-studio/how-to/evaluate-generative-ai-app)
- [Fine-tune a model](https://learn.microsoft.com/azure/ai-studio/how-to/fine-tune-model-llama)
