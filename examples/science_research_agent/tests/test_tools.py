"""Offline tests for the science research agent's external tools."""

import os
import unittest
from unittest.mock import patch

from examples.science_research_agent.tools import Tools


class WebSearchTests(unittest.TestCase):
    @patch("tavily.TavilyClient")
    def test_web_search_uses_tavily_client(self, client_class):
        client_class.return_value.search.return_value = {
            "results": [{
                "title": "Public result",
                "url": "https://example.test/result",
                "content": "Public research summary",
            }],
        }

        with patch.dict(
            os.environ,
            {"TAVILY_API_KEY": "test-key", "ASSERT_AI_REAL_TOOLS_NOCACHE": "1"},
        ):
            result = Tools().web_search("public research", max_results=3)

        client_class.assert_called_once_with(api_key="test-key")
        client_class.return_value.search.assert_called_once_with(
            query="public research",
            max_results=3,
            include_raw_content=False,
            search_depth="basic",
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["results"][0]["title"], "Public result")


if __name__ == "__main__":
    unittest.main()
