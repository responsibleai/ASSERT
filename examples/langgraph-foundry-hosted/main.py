# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Foundry hosted-agent server entrypoint for the travel planner graph.

Hosts the compiled LangGraph graph on Foundry over the Responses protocol
using ``langchain_azure_ai.agents.hosting.ResponsesHostServer``. Conversation
state is managed server-side by the platform via ``previous_response_id`` — no
application-side session storage is needed.
"""

from __future__ import annotations

import os

from langchain_azure_ai.agents.hosting import ResponsesHostServer

from agent import get_graph


def main() -> None:
    graph = get_graph()
    port = int(os.environ.get("PORT", "8088"))
    ResponsesHostServer(graph).run(port=port)


if __name__ == "__main__":
    main()
