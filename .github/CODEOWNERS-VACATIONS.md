# CODEOWNERS Vacation Calendar

This file lists vacation, leave, and other planned-unavailability windows for repo CODEOWNERS. The **dev-maintainer agent** reads this file when picking a reviewer to ping, and skips any owner whose window covers the current date.

**Format:** one row per unavailability window. Use ISO 8601 dates (`YYYY-MM-DD`). End date is inclusive.

| Owner (@handle) | Start | End | Notes |
|---|---|---|---|
| @minthigpen | 2026-06-15 | 2026-06-26 | Vacation |
| @changliu2 | 2026-06-05 | 2026-06-27 | Vacation (~3 weeks) |

## How to update this file

- **Add a row** when you have a planned absence ≥ 2 business days. Shorter absences are not worth coordinating around.
- **Remove a row** after you return, or once the end date has passed. Stale entries cause the agent to skip you unnecessarily.
- The dev-maintainer agent reads this on every observation pass; updates take effect on the next pass (cadence configured per-operator).

## Routing impact

When the dev-maintainer agent picks a reviewer for a PR, it walks [`.github/CODEOWNERS`](CODEOWNERS) and applies these exclusions in order:

1. Exclude the PR author.
2. Exclude any owner with an active row in this file.
3. Exclude `@changliu2` unless every other eligible owner has been excluded.
4. From what remains, prefer the owner pinged least recently for the affected path.

See [`.github/agents/dev-maintainer.md`](agents/dev-maintainer.md) and the `Reviewer routing logic` section of [`AGENTS.md`](../AGENTS.md) for the full rules.
