# Setup checklist — Clarity MCP ⇄ ASSERT (in-IDE only)

These steps require a real IDE with MCP support (VS Code + Copilot agent mode,
Claude Code, or Cursor) and cannot be completed from a headless terminal. Do them
once per workspace, then the `run-assert-eval` skill's discovery front door
(`run_clarity`) becomes callable.

## Phase 1 — Environment setup

- [ ] **Install and embed Clarity with the pinned bootstrap** (run with Python
      3.12+ from the target repo):
      ```
      python .claude/skills/run-assert-eval/setup_clarity.py .
      ```
      - First install: Clarity currently bundles every provider, so allow 5-10
        minutes. Later workspaces reuse the user-cache tool environment.
      - This is intentionally **not** an ASSERT core dependency or normal extra:
        Clarity is not on PyPI and requires Python 3.12 while ASSERT supports
        3.11.
      - Verify `.vscode/mcp.json` points directly at the cached Clarity
        environment's Python with `-m clarity_agent.mcp`. It must not use a
        second source checkout or a globally installed `uv`.
- [ ] **Confirm the server starts.** The bootstrap does this automatically with
      `python -m clarity_agent.mcp --help`; treat a non-zero exit as setup
      failure.
- [ ] **Verify ASSERT**: `assert-ai --help`, and a smallest-sample dry run of one
      repo example config is invocable.
- [ ] **Reload MCP servers** in the IDE so the `clarity-agent` tools appear, then
      confirm you can call `run_clarity`.
- [ ] **Do _not_ commit `.vscode/mcp.json`.** It contains an absolute path to
      your cached tool environment. It is gitignored; each developer runs the
      bootstrap to generate their own.

## Phase 2 — End-to-end verification (definition of done)

- [ ] **Fresh discovery**: with no `.clarity-protocol/failures/failures.md`, call
      `run_clarity`, conduct a short clarifying conversation, and confirm
      `failures.md` gets written.
- [ ] **Parser**: `python .claude/skills/run-assert-eval/clarity_intake.py .clarity-protocol`
      emits candidate behaviors; run the unit tests:
      ```
      python -m pytest .claude/skills/run-assert-eval/tests/test_clarity_intake.py
      ```
- [ ] **Five-case smoke**: from an existing `failures.md`, the workflow presents
      triage, you pick one P1, **exactly one** config is generated with a
      variants-derived dimension, and the agent runs 5 prompt cases (scenario
      disabled, concurrency 5) under run name `smoke`. `results status` and
      `results status --json` must render immediately afterwards.
- [ ] **Full baseline opt-in**: after showing smoke results, the agent asks
      whether to run 25 prompt + 25 scenario cases. It never silently launches a
      two-hour loop.
- [ ] **Two failures → two configs**: selecting two failures produces two separate
      configs and two sequential runs — never one merged config.
- [ ] **Decline at triage → zero writes**: declining at the triage gate results in
      zero files written and zero runs.
- [ ] **Loop-close**: a `record_suggestion` round-trip lands in the Clarity mailbox
      after a completed run.

## Notes

- Copilot agent mode supports MCP **tools** only (not `clarity://…` resources).
  `read_protocol_document` covers the same ground as the resource endpoints.
- Never read/print/commit `.env` or credential values — reference env var **NAMES**
  only (AZURE_API_KEY, AZURE_API_BASE, OPENAI_API_KEY, GITHUB_TOKEN,
  ANTHROPIC_API_KEY, azure_ad_token).
- Do not edit inside the Clarity-managed block in `AGENTS.md`
  (between `<!-- clarity-begin -->` and `<!-- clarity-end -->`).
- **Preserving `.clarity-protocol/`**: this repo gitignores it because the protocol
  describes a *system-under-test*, not this framework — it's per-target runtime
  output. In **your own product's repo**, the protocol describes your product, so
  prefer committing the durable docs (`goal/`, `solution/`, `failures/`) and
  ignoring only `transcripts/` (and optionally `mailboxes/`). In this framework
  repo, do not copy generated discovery workspaces into `examples/`. Before a new
  discovery run overwrites the scratch directory, offer to export it to a
  user-owned location outside the example tree. Commit only the curated atomic
  config and README needed to run the example.
