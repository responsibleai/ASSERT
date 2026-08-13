# Some Heading That Is Not A Failure Title

This document has no `# Failure:` title, no `## Summary`, no `## Observations`,
no `## Failure Chain`, and no `**Variants:**`. The parser should not crash on it,
should not drop it, and should attach a warning while keeping whatever fields it
could recover.

## Random Section

Prose that does not match any expected header.
