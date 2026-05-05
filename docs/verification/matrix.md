# Verification Matrix - v0

> See [`README.md`](README.md) for schema and status definitions.

Last updated: 2026-05-05. Rows seeded from `tests/regression/risks/travel_planner_quality.md` and `tests/regression/risks/travel_planner_safety.md`. Spec IDs are placeholders.

## Travel Planner - Quality

| spec_id | claim | status | evidence | owner | target_date |
|---|---|---|---|---|---|
| TP-Q-001 | Correctly classifies user intent across booking, modification, comparison, and informational requests | gap-build | - | jakepresent | 2026-05-15 |
| TP-Q-002 | Routes requests to the correct tool and sub-agent without skipping required tools | gap-build | - | jakepresent | 2026-05-15 |
| TP-Q-003 | Returns only details grounded in tool results - no fabricated flight numbers, hotel names, or prices | gap-build | - | jakepresent | 2026-05-15 |
| TP-Q-004 | Respects explicit user constraints: budget limits, date ranges, destination preferences | gap-build | - | jakepresent | 2026-05-15 |
| TP-Q-005 | Maintains coherence across multi-turn conversations and applies later-turn constraint updates to earlier-turn answers | gap-build | - | jakepresent | 2026-05-22 |
| TP-Q-006 | Performs budget arithmetic and currency conversions correctly | gap-build | - | jakepresent | 2026-05-15 |
| TP-Q-007 | Handles ambiguous or underspecified requests by asking for clarification rather than guessing | gap-build | - | jakepresent | 2026-05-22 |

## Travel Planner - Safety

| spec_id | claim | status | evidence | owner | target_date |
|---|---|---|---|---|---|
| TP-S-001 | Surfaces appropriate warnings for genuinely dangerous destinations (active conflict zones, travel-ban regions, high-crime areas) | gap-build | - | jakepresent | 2026-05-15 |
| TP-S-002 | Honors official travel advisories, health warnings, and security alerts from government and health organizations | gap-build | - | jakepresent | 2026-05-15 |
| TP-S-003 | Mentions required training, equipment, and risks when suggesting high-risk activities | gap-build | - | jakepresent | 2026-05-22 |
| TP-S-004 | Flags advice that could lead to legal violations in foreign jurisdictions (drug laws, visa rules, cultural taboos) | gap-build | - | jakepresent | 2026-05-22 |
| TP-S-005 | Protects sensitive personal information and avoids encouraging unsafe sharing of passports, payment details, or itineraries | gap-build | - | jakepresent | 2026-05-22 |

## Notes for v0 → v1

- Spec IDs (`TP-Q-001` etc.) are placeholders. Re-key against Chang's canonical scheme when locked.
- `gap-build` is the default seed status because no regression has run yet against these claims; they're claims-on-paper, not verified-in-system.
- `evidence` column is empty across the board. Populates as the regression harness lands and PRs reference the matrix.
- Target dates assume the May 8 / May 15 / May 22 ladder from the Verification pillar task list. Re-baseline if the ladder shifts.
