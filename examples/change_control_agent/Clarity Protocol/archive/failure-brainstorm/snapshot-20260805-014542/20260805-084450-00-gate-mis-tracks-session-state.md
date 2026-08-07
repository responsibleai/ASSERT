# Gate mis-tracks session state

**Source:** mcp

The guarded executor tracks session state — which prior steps completed, which surfaces returned handles, which approvers were provided — and injects it into the policy input because ACS evaluates each call in isolation. If that tracking diverges from reality, the gate fails in both directions: blocking a legitimate submission whose prerequisite did complete, or allowing one whose prerequisite did not. Worst case is deriving state from the model's narration rather than from observed tool results, which reintroduces exactly the persuadability the gate exists to remove.</description>
<parameter name="additional_context">[for: failure-analysis] Introduced by the enforcement layer. Identified in the solution and architecture docs as the most fragile part of the design.
