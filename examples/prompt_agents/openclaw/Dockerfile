FROM node:24-bookworm

RUN npm install -g openclaw@latest

RUN mkdir -p /root/.openclaw/workspace /root/.openclaw/agents/main/agent

COPY SOUL.md /root/.openclaw/agents/main/agent/SOUL.md

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["sleep", "infinity"]
