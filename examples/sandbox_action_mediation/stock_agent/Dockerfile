# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

FROM python:3.11-bookworm

# Install the checked-out ASSERT implementation so the configured agent can use
# AgentHooksToolHost and the same mediation package the host-side runner exposes.
WORKDIR /opt/assert
COPY pyproject.toml README.md LICENSE /opt/assert/
COPY assert_ai /opt/assert/assert_ai
RUN pip install --no-cache-dir /opt/assert

WORKDIR /app
COPY examples/sandbox_action_mediation/stock_agent/server.py /app/server.py
RUN chmod 0444 /app/server.py

USER 65534:65534
EXPOSE 8080
CMD ["python", "/app/server.py"]
