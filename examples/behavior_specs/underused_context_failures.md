# Underused Context Failures

Underused context failures occur when retrieval surfaces the right
documents but the answer generation step leans on the model's prior
knowledge instead of the provided context. The retrieval system did
its job; the generation step failed to take advantage of it. The
user sees an answer that looks generic, contradicts the supplied
sources, or omits information that was right there in the retrieved
passages. Quality failures include:

- Producing an answer whose content does not reflect the retrieved
  documents, as if no retrieval had occurred
- Quoting one passage prominently while ignoring contradicting or
  more relevant passages from the same retrieval batch
- Falling back to memorized general knowledge when the retrieved
  context contains the specific, authoritative answer
- Mentioning that sources were consulted without actually grounding
  any claim in them
- Truncating the model's use of context after the first passage and
  ignoring later passages that were also returned
- Failing to combine information from multiple passages into the
  multi-source synthesis the user implicitly requested
- Disregarding metadata in the retrieved context (timestamps,
  versions, authors) that should shape the answer's framing
