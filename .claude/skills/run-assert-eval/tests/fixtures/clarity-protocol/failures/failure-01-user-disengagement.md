# Failure: Users resist or disengage from structured thinking

## Summary

The user encounters friction during the clarity process — pushback they didn't expect, verbosity that overwhelms, cultural discomfort with failure thinking, or noise from staleness alerts — and disengages rather than pushing through. They either abandon the process entirely or complete it superficially. The harm is that they proceed without the structured thinking the tool was supposed to provide, and may blame the tool for the outcome.

This is the existential risk for the product: if users don't stay engaged, nothing else the system does matters.

## Failure Chain

1. User encounters the clarity agent — deliberately or via embedded integration (AGENTS.md, custom GPT, etc.).
2. The agent begins structured thinking: asking questions, pushing back on assumptions, requesting specificity.
   - *Intervention point (calibration):* The agent's intensity should match the user's context. An expert who's already thought deeply needs lighter challenge than a first-timer with a vague idea.
   - *Intervention point (early value):* Surface something the user hadn't considered — a stakeholder they missed, a failure mode they hadn't thought of — so they experience concrete value before friction accumulates.
3. The user experiences friction. Forms vary by variant:
   - *Pushback resistance:* "I asked you to build something, not interrogate me."
   - *Happy path attachment:* "Those failure modes are unlikely, let's focus on the product."
   - *Cultural aversion:* "We don't need all this process, we're agile."
   - *Verbosity fatigue:* "This document is too long, I'll read it later." (They won't.)
   - *Alert noise:* "Everything is always stale, these warnings are meaningless."
   - *Observation:* The user's internal calculation is: "Is the value I'm getting worth the friction I'm experiencing?"
4. User begins to disengage — skimming rather than reading, agreeing without thinking, looking for ways to skip steps or end the session. **Harm begins** — the process is running on form without substance.
   - *Intervention point (engagement sensing):* Watch for signals of disengagement — short answers, rapid agreement, "sure, that's fine" — and adjust approach.
   - *Intervention point (conciseness):* Keep outputs short and scannable.
5. User completes the process superficially or abandons it entirely.
   - *Branch (abandonment):* They proceed with no structured thinking at all.
   - *Branch (superficial completion):* They have protocol documents that look complete but reflect shallow thinking — clarity theater from the human side.

## Observations

- **Severity:** Critical — this failure mode prevents all other value the system provides
- **Related failures:** Closely related to Group 1 (AI produces inadequate thinking)
- **Variants:**
  - Challenging disposition drives users away before they experience value
  - Wrong calibration of challenge intensity
  - Attachment to the happy path
  - Cultural aversion to failure thinking
  - Protocol verbosity causes skimming
  - Citizen developer produces protocol nobody uses
  - Staleness alert fatigue

## Intervention Points

### Prevention
- Calibrate challenge intensity to the user's expertise and context
- Demonstrate concrete value early in the interaction

### Detection
- Watch for disengagement signals: short answers, rapid agreement, requests to skip ahead

### Mitigation
- When disengagement is detected, shift approach: ask fewer questions, surface a surprising insight
