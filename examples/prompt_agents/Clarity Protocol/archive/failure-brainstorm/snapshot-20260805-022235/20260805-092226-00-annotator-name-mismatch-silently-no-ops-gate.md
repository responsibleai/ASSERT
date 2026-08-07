# Annotator name mismatch silently no-ops gate

**Source:** mcp

The annotator name must match across the manifest key, the input.annotations reference in policy, and the dispatcher branch. A mismatch does not raise. The rule never fires, the run reports enforcement active, and the metrics show a governed target that was never governed.
