#!/bin/bash
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

set -e

AZURE_BASE_URL="${AZURE_OPENAI_ENDPOINT%/}"
if [ -n "$AZURE_BASE_URL" ]; then
    case "$AZURE_BASE_URL" in
        */openai/v1) ;;
        *) AZURE_BASE_URL="$AZURE_BASE_URL/openai/v1" ;;
    esac
fi

# Write Azure OpenAI config at runtime so env vars are available
CONFIG_FILE="/root/.openclaw/openclaw.json"
cat > "$CONFIG_FILE" <<EOF
{
  "agents": {
    "defaults": {
      "model": {
        "primary": "azure-openai/gpt-4o-mini"
      }
    }
  },
  "models": {
    "providers": {
      "azure-openai": {
        "baseUrl": "${AZURE_BASE_URL}",
        "api": "openai-completions",
        "models": [{"id": "gpt-4o-mini", "name": "gpt-4o-mini"}]
      }
    }
  }
}
EOF

# Write Azure OpenAI auth into OpenClaw's auth store
AUTH_FILE="/root/.openclaw/agents/main/agent/auth-profiles.json"
if [ -n "$AZURE_OPENAI_API_KEY" ] && [ ! -f "$AUTH_FILE" ]; then
    mkdir -p "$(dirname "$AUTH_FILE")"
    cat > "$AUTH_FILE" <<EOF
{
  "version": 1,
  "profiles": {
    "azure-openai:default": {
      "type": "api_key",
      "provider": "azure-openai",
      "key": "$AZURE_OPENAI_API_KEY"
    }
  }
}
EOF
fi

exec "$@"
