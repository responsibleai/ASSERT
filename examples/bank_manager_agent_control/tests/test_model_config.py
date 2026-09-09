"""Offline tests for the Bank Manager model routing contract."""

import _bootstrap  # noqa: F401

import os
import unittest
from unittest.mock import patch

import bank_agent_common as agent


class ModelConfigTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(
            os.environ,
            {
                "AZURE_API_BASE": "https://example.openai.azure.com/",
                "AZURE_API_KEY": "test-key",
            },
            clear=False,
        )
        self.environment.start()

    def tearDown(self):
        self.environment.stop()

    @patch("bank_agent_common.AzureChatOpenAI")
    def test_gpt_model_uses_azure_openai_route(self, model_class):
        with patch.dict(os.environ, {"AGENT_MODEL": "gpt-4o-mini"}):
            agent._build_llm()

        model_class.assert_called_once_with(
            azure_deployment="gpt-4o-mini",
            azure_endpoint="https://example.openai.azure.com/",
            api_version="2024-12-01-preview",
            max_tokens=4000,
            api_key="test-key",
            temperature=0.0,
        )

    @patch("langchain_azure_ai.chat_models.AzureAIChatCompletionsModel")
    def test_non_gpt_model_uses_azure_ai_inference_route(self, model_class):
        # Non-GPT deployments (DeepSeek, Mistral, Llama, Phi, Cohere, ...) are
        # served via Azure AI Inference rather than the Azure OpenAI gateway,
        # because those deployments typically run behind SGLang/vLLM, which
        # require the `model` request-body field AzureAIChatCompletionsModel
        # populates. `_build_llm` imports the class locally (inside the
        # non-GPT branch), so it must be patched at its defining module, not
        # at `bank_agent_common`.
        with patch.dict(os.environ, {"AGENT_MODEL": "DeepSeek-V3"}):
            agent._build_llm()

        model_class.assert_called_once_with(
            endpoint="https://example.openai.azure.com/models",
            credential="test-key",
            model="DeepSeek-V3",
            temperature=0.0,
            max_tokens=4000,
        )

    # A prior version of this suite also asserted the exact wire-format HTTP
    # request (path, body) for the non-GPT route, back when it went through
    # `ChatOpenAI` against an OpenAI-v1-compatible endpoint. That routing was
    # replaced by `AzureAIChatCompletionsModel`, backed by the Azure AI
    # Inference SDK rather than a plain httpx-mockable OpenAI client, so the
    # old request-shape assertion no longer applies and doesn't have a
    # like-for-like replacement without exercising the real SDK client
    # end-to-end (a live/integration test, not this offline suite). The
    # constructor-args check above is the coverage this suite can offer
    # without live credentials.


if __name__ == "__main__":
    unittest.main()