# Annotator name mismatch silently no-ops

**Source:** mcp

The annotator name does not match across the manifest key, the input.annotations reference in the Rego, and the host dispatcher branch. The gate no-ops, every claim passes, and the run reports enforcement as active.
