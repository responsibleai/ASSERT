# Regeneration loops or degrades to an empty answer

**Source:** mcp

A denied response triggers regeneration, and the regenerated response is denied again. Each cycle costs a further model call on a turn that was already failing. If the loop is unbounded the turn never completes; if it degrades bluntly, the traveller receives a stripped, content-free answer to a reasonable request. Either way the worst experience lands on exactly the users whose questions were hardest to ground.</description>
<parameter name="additional_context">[for: architecture-design] Argues for a bounded retry with a useful degraded form — leading with supported content and marking the rest unconfirmed — rather than an unbounded loop or a flat refusal.
