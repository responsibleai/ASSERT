# CI safety gate

Use [`changliu2/assert-ai-action`](https://github.com/changliu2/assert-ai-action) to run ASSERT in pull requests and fail on safety regressions.

The fastest setup path is the action's agent bootstrap:

```text
read https://raw.githubusercontent.com/changliu2/assert-ai-action/main/ONBOARD.md
```

Generated workflows should call `changliu2/assert-ai-action@v1`. Keep provider credentials in CI secrets and reference environment variable names only.
