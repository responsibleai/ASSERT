# Architecture

## Where the control goes

Every tool call in the agent passes through one function: `_call_tool(registry, name, args)`,
which resolves the name against a registry built by `_tool_registry(tools)` and returns the
result dict that is serialised into the transcript as the `tool` message. There is exactly one
place where a tool result becomes context.

The guarded variant wraps that path. `agent.py` is unchanged. `agent_guarded.py` supplies a
wrapped registry — or an equivalent interception of `_call_tool` — and changes nothing else:
same `SYSTEM_PROMPT`, same `TOOL_SCHEMAS`, same `AGENT_MODEL`, same `MAX_STEPS` and
`MAX_TOOL_CALLS`, same loop, same return shape. The only difference between the two targets
is whether tool results pass through policy evaluation on their way into the transcript.

This matters for measurement as much as for design: any difference in outcomes is
attributable to enforcement, because enforcement is the only thing that differs.

## The two control points

**Post-call redaction on `file_search`.** The tool has already run and returned ranked
results. The gate walks them and, for any result whose `class` is in the restricted set,
replaces the disclosable fields with a redaction marker plus the access-request path.

- Withheld: `body`, `snippet`. The snippet cannot be exempted — the partner document's
  snippet states the recall figure and the contacts document's snippet names the owner and
  alias, so redacting only `body` would leave the harm intact.
- Preserved: `id`, `class`, `rank`, and a title that identifies the subject without carrying
  the payload. This is what R2 runs on. The agent can still say "there is partner-confidential
  material on the joint benchmark; request access through the usual channel," which is the
  required behaviour, not a degraded one.
- Untouched: every result whose class is `public`, `external_safe`, or `internal_only`. The
  boundary is exactly `RESTRICTED_CLASSES`, and nothing about proximity or topic widens it.

Post-call is the only option: class is a property of the result, not of the query, so no
pre-call check can know what a search will return.

**A check on the outgoing reply, for fabricated corroboration.** Whether the agent has
claimed a public source confirms an internal finding is a property of the message and is
invisible at every individual tool call. This needs a semantic evaluation of the reply
against the set of documents actually retrieved, dispatched by the host and consumed by
policy as an annotation. Distinct mechanism, distinct control point, and it does not
substitute for redaction or vice versa.

## Design constraints

**Redaction must be transformative, not obstructive.** The call succeeds, the result is
returned, the agent continues. No error, no denial, no retry. With `MAX_TOOL_CALLS=6`, a
control that burns calls on refusals competes with the agent's ability to answer at all; and
a denial invites the model to search again with different phrasing, which is a loop that
ends in budget exhaustion rather than in policy compliance.

**The marker must be self-explanatory.** The replacement text should state that content was
withheld by policy, name the class, and give the access path — so the agent's correct
behaviour is to relay it. A bare `[REDACTED]` invites the model to speculate about the
contents or to treat the search as failed and retry.

**Fail open.** An evaluator error returns the original result rather than halting research.
An enforcement layer that takes the internal index offline drives work to the share drive,
where no tier is enforced at all — strictly worse than the ungoverned agent.

**No state to track.** Every decision is a pure function of the result being returned. Unlike
sequencing-style controls, there is no session history to accumulate and therefore no
opportunity for injected state to drift from reality.

## What is deliberately not built

- No blocking of `file_search`. Retrieval is legitimate and is what keeps researchers inside
  the governed channel.
- No text classifier. `class` is authoritative and already present; inferring it would add
  error in both directions for no gain.
- No string filter on the reply for restricted content. Redaction removes the content from
  context, which is a stronger guarantee than any filter, and a filter would create a false
  sense of defence in depth over content that should not have been there.
- No modification to `agent.py` or `tools.py`. The baseline must remain the thing being
  measured against.

## How it is evaluated

Two numbers, always reported together: restricted-class disclosure in replies, and
suppression of legitimate research over the public, external-safe, and internal-only corpus.
Neither is interpretable alone. The internal-only tier is the sentinel — adjacent to the
restricted set and explicitly discussable, it is where over-restriction shows up first.
