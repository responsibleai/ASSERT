# Tool Parameter Formatting Failures

Tool parameter formatting failures occur when an agent picks the
correct tool but produces an argument payload that is malformed,
incomplete, or semantically wrong. The tool either rejects the call,
silently does the wrong thing, or returns a confusing error that the
agent then has to interpret. These failures show up as fragile,
brittle agent behavior even when the high-level plan is sound.
Quality failures include:

- Omitting a required argument and either guessing a default or
  sending the call anyway
- Passing a value of the wrong type (e.g., a string where the schema
  requires an integer, a single value where a list is required)
- Sending values in the wrong unit, format, or convention (e.g.,
  "next Friday" instead of an ISO date, miles instead of kilometers)
- Misnaming a parameter (typo, casing difference, deprecated alias)
  so the tool ignores or rejects it
- Embedding multiple logical arguments into one field (e.g., putting
  "city, country" in a `city` parameter)
- Sending unescaped or improperly quoted strings that break the
  tool's parser
- Passing values that the schema allows but the underlying system
  cannot handle (e.g., out-of-range numbers, invalid IDs, expired
  tokens)
