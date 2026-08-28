# Talks

Decks for ASSERT talks, as single-file PDFs — download and open in any PDF
viewer. No browser, build step, or sibling folders required.

| Talk | Deck | Length |
|---|---|---|
| **AI Engineer World's Fair 2026** — *evaluate, control, optimize your AI agents* | [`aiewf-18min/aiewf-2026-deck.pdf`](aiewf-18min/aiewf-2026-deck.pdf) | 18 min · 7 slides |

The revised AIEWF deck follows the current
[`bank_manager_agent_control`](../examples/bank_manager_agent_control/README.md)
example: ASSERT discovers two different runtime failures from written
requirements, ACS applies the appropriate structural control, and the comparison
measures both impermissible and permissible behavior. The deck preserves a
historical viewer snapshot: Behavior 1 is `8% / 0%` baseline, `6% / 0%`
defensive prompt, and `0% / 0%` ACS Rego; Behavior 2 is `8% / 27%` baseline,
`0% / 47%` hardened prompt, and `0% / 27%` ACS classifier. Raw source runs,
traces, and model environment were not committed, so rerun before using those
percentages as evidence for the current hardened runtime.

Detailed setup and policy mechanics live at the relative
[demo landing page](../examples/bank_manager_agent_control/docs/README.md), so
the repository does not depend on a pinned commit URL.
