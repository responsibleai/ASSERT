FROM node:24-bookworm

RUN npm install -g openclaw@latest

# The agent under evaluation executes untrusted model output, so it does not run
# as root. A fixed uid keeps volume ownership stable across rebuilds.
RUN useradd --create-home --uid 10001 agent

ENV HOME=/home/agent
ENV OPENCLAW_HOME=/home/agent/.openclaw

RUN mkdir -p "$OPENCLAW_HOME/workspace" "$OPENCLAW_HOME/agents/main/agent" \
    && chown -R agent:agent /home/agent

COPY --chown=agent:agent SOUL.md /home/agent/.openclaw/agents/main/agent/SOUL.md

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER agent

ENTRYPOINT ["/entrypoint.sh"]
CMD ["sleep", "infinity"]
