"""Offline tests for the Bank Manager model routing contract."""

import _bootstrap  # noqa: F401

import os
import unittest
from unittest.mock import patch

import agent


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

    @patch("agent.AzureChatOpenAI")
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

    @patch("agent.ChatOpenAI")
    def test_non_gpt_model_uses_inference_compatible_route(self, model_class):
        with patch.dict(os.environ, {"AGENT_MODEL": "DeepSeek-V3"}):
            agent._build_llm()

        model_class.assert_called_once_with(
            base_url="https://example.openai.azure.com/models",
            api_key="test-key",
            model="DeepSeek-V3",
            temperature=0.0,
            max_tokens=4000,
        )


if __name__ == "__main__":
    unittest.main()