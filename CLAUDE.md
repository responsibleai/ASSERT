# Claude Code instructions

Read [`AGENTS.md`](AGENTS.md) first. It is the source of truth for how coding agents should help with this private-preview repository.

Never read, print, commit, or infer secrets from `.env` or other local environment files.

## Skills

This repository provides Claude Code skills in `.claude/skills/`:

- **run-assert-eval**: Run an end-to-end ASSERT evaluation from a plain-language requirement. Config generation is evidence-backed — every behavior category, stratify dimension, and judge dimension is researched against retrieved primary sources, deduplicated across `N` passes, and approved by the user before a cited `examples/<slug>/eval_config.yaml` is written. See [`.claude/skills/run-assert-eval/SKILL.md`](.claude/skills/run-assert-eval/SKILL.md).
