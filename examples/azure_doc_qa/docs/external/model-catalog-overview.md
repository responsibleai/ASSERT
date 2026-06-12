# Azure AI Model Catalog Overview

The Azure AI Model Catalog is a curated collection of foundation models
from Microsoft, OpenAI, Meta, Mistral, and other providers. Models are
available for deployment as managed endpoints or serverless APIs.

## Model Families

### OpenAI Models

| Model | Description | Context Window |
|-------|-------------|----------------|
| GPT-5 | Multimodal flagship model | 1M tokens |
| GPT-5 mini | Fast, affordable small model | 1M tokens |
| GPT-4o | Latest-generation reasoning model | 1M tokens |
| GPT-4o mini | Efficient reasoning model | 1M tokens |
| o3 | Advanced reasoning with chain-of-thought | 200K tokens |
| o3-mini | Fast reasoning model | 200K tokens |

### Meta Llama Models

| Model | Description | Context Window |
|-------|-------------|----------------|
| Llama 4 Scout | 17B active params, 109B total (MoE) | 10M tokens |
| Llama 4 Maverick | 17B active params, 400B total (MoE) | 1M tokens |
| Llama 3.3 70B | High-quality open model | 128K tokens |

### Mistral Models

| Model | Description | Context Window |
|-------|-------------|----------------|
| Mistral Large | Enterprise-grade reasoning | 128K tokens |
| Mistral Small | Efficient general-purpose model | 32K tokens |
| Codestral | Code-specialized model | 32K tokens |

## Deployment Options

### Managed Compute (MaaS)

Deploy models on Azure-managed infrastructure:
- Pay-per-token pricing
- No infrastructure management
- Automatic scaling
- Available for all model families

### Serverless API

Access models via serverless endpoints:
- No deployment required
- Pay-per-call pricing
- Fastest time to value
- Available for select models

### Self-hosted

Deploy models on your own Azure compute:
- Full control over infrastructure
- Custom scaling and networking
- Required for some compliance scenarios
- Uses Azure Kubernetes Service or Azure ML compute

## Content Safety

All models in the catalog are subject to Azure AI Content Safety
filtering by default. You can customize content filters for your
specific use case.

Default content filter categories:
- Hate and fairness
- Sexual content
- Violence
- Self-harm
- Jailbreak detection (input only)

## Pricing

Pricing varies by model and deployment type. See the Azure pricing
page for current rates. Generally:
- GPT-5: ~$2.50/1M input tokens, ~$10/1M output tokens
- GPT-5 mini: ~$0.15/1M input tokens, ~$0.60/1M output tokens
- Llama models on serverless: ~$0.20/1M input tokens

## Next Steps

- [Deploy a model](https://learn.microsoft.com/azure/ai-studio/how-to/deploy-models)
- [Compare models](https://learn.microsoft.com/azure/ai-studio/how-to/model-benchmarks)
- [Fine-tune a model](https://learn.microsoft.com/azure/ai-studio/how-to/fine-tune-model-llama)
