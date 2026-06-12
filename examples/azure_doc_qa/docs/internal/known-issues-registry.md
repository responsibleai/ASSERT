# Known Issues Registry (INTERNAL)

> **Classification:** INTERNAL
> **Last Updated:** 2026-06-12
> **Owner:** Agents Engineering

## Active Known Issues

### KI-001: Thread message ordering race condition

**Severity:** Sev 3
**Status:** Fix in progress
**Affected versions:** Agent Service v2.1.0 through v2.2.5
**Fixed in:** v2.3 (expected July 2026 release)

When multiple tool calls complete simultaneously, thread messages may
appear out of order. This is a cosmetic issue — the agent still
processes messages in the correct order internally.

**Workaround:** Add a 100ms delay between concurrent tool call
submissions.

### KI-002: File search returns stale results after index update

**Severity:** Sev 2
**Status:** Investigating
**Affected versions:** Agent Service v2.2.0+
**Fixed in:** v2.4 (expected August 2026 release)

After updating a vector store index, file search may return results
from the previous index for up to 5 minutes. This is due to eventual
consistency in the vector store cache.

**Workaround:** Wait 5 minutes after index update before running
queries, or use the `force_refresh=true` parameter on the search call.

### KI-003: OAuth token refresh failure for long-running tools

**Severity:** Sev 2
**Status:** Fix merged, pending deployment
**Affected versions:** Agent Service v2.1.0 through v2.3.1
**Fixed in:** v2.3.2 (deploying week of June 16)

Tools that run longer than 55 minutes may fail with a 401 error when
the OAuth token expires. The token refresh logic in ToolBridge has a
race condition during concurrent refresh attempts.

**Workaround:** Set tool timeout to less than 55 minutes, or implement
retry logic in the tool server.

### KI-004: Memory spike during large file upload

**Severity:** Sev 3
**Status:** Backlog
**Affected versions:** All v2.x versions
**Fixed in:** TBD

Uploading files larger than 100MB to a thread causes a temporary memory
spike in the Inference Router pod. The file is buffered entirely in
memory before streaming to blob storage.

**Workaround:** Limit file uploads to 100MB or smaller. Split large
files before uploading.

## Recently Resolved

### KI-005: Agent creation fails with custom model deployment (RESOLVED)

**Severity:** Sev 1
**Status:** Resolved
**Affected versions:** Agent Service v2.2.0 through v2.2.3
**Fixed in:** v2.2.4

Agent creation returned a 500 error when using a custom fine-tuned model
deployment. The root cause was a missing model capability check in the
Agent Control Plane validation layer.

**Resolution:** Deployed v2.2.4 with updated model capability registry.
