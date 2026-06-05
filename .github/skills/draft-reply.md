# draft-reply

## Purpose

Draft a response in Chang's voice for the comms agent to place in the comms inbox. The draft is not posted externally. It is a reviewable artifact for Chang, the sole human approver.

## When to use

Use this skill when the comms agent needs a concise reply to a public issue, discussion, user question, release note comment, or community thread.

## Voice principles

- Lead with the answer.
- Use evidence before interpretation.
- Be direct, measured, and specific.
- Prefer tables over long prose when comparing options.
- State what not to do when the boundary matters.
- Avoid hype. Make the concrete next step easy.
- Use repo terms: behavior, eval spec, dataset, test cases, execute, judge, OpenTelemetry, OpenInference trace attributes, spec-driven scoring.

## Anti-patterns to avoid

- Marketing language: "revolutionary", "game-changing", "best-in-class".
- Hedging without evidence: "maybe", "probably", "it seems".
- Overpromising roadmap or support timelines.
- Rewriting the user's concern into a generic compliment.
- Long setup before the answer.
- Internal-only context, private prioritization, or non-public commitments.
- Naming competitors unless the user already did and the reply needs a factual distinction.

## Draft structure

1. **Answer first:** one sentence that resolves the user's main question.
2. **Reasoning:** one to three sentences with concrete evidence, command, artifact, or constraint.
3. **Optional table:** use only when choices or trade-offs are clearer in rows.
4. **What not to do:** add when the user is likely to take an unsafe or unsupported path.
5. **Next step:** one command, file, or question for the user.

## Example draft snippet

Scenario: A user asks whether they should judge only final answers for a LangGraph agent.

Draft:

> Short answer: no — for a non-trivial agent, capture OpenTelemetry spans and judge against the OpenInference trace attributes as well as the final response.
>
> Final text can tell you whether the answer looks right. Traces tell you why it happened: tool calls, routing, retries, model calls, and latency. In ASSERT, use `target.callable` with `target.trace`, then inspect `inference_set.jsonl`, `scores.jsonl`, and `metrics.json`.
>
> Do not replace trace capture with hand-written summaries of tool behavior. That removes the evidence the judge needs.
