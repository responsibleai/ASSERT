"""Offline tests for the Bank Manager model routing contract."""

import _bootstrap  # noqa: F401

import json
import os
import unittest
from unittest.mock import patch

import httpx
from langchain_openai import ChatOpenAI

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
    def test_non_gpt_model_uses_openai_v1_route(self, model_class):
        with patch.dict(os.environ, {"AGENT_MODEL": "DeepSeek-V3"}):
            agent._build_llm()

        model_class.assert_called_once_with(
            base_url="https://example.openai.azure.com/openai/v1",
            api_key="test-key",
            model="DeepSeek-V3",
            temperature=0.0,
            max_tokens=4000,
        )

    @patch("agent.ChatOpenAI")
    def test_non_gpt_model_sends_openai_v1_chat_request(self, model_class):
        with patch.dict(os.environ, {"AGENT_MODEL": "DeepSeek-V3"}):
            agent._build_llm()

        requests = []

        def handle_request(request):
            requests.append(request)
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl-test",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "DeepSeek-V3",
                    "choices": [{
                        "index": 0,
                        "message": {"role": "assistant", "content": "Hello"},
                        "finish_reason": "stop",
                    }],
                },
            )

        transport = httpx.MockTransport(handle_request)
        model = ChatOpenAI(
            **model_class.call_args.kwargs,
            http_client=httpx.Client(transport=transport),
            http_async_client=httpx.AsyncClient(transport=transport),
        )
        model.invoke("Hello")

        self.assertEqual(requests[0].url.path, "/openai/v1/chat/completions")
        self.assertEqual(json.loads(requests[0].content)["model"], "DeepSeek-V3")


if __name__ == "__main__":
    unittest.main()