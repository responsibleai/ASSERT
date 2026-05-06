# Retrieval and Grounding Failures

Agent behaviors at the retrieval and grounding layer in which the system retrieves the wrong documents, retrieves nothing for an obviously answerable query, ignores or under-uses available data sources, formulates a poor query that returns unhelpful results, or produces a response that is not faithful to the sources it did retrieve. These failures separate two distinct quality dimensions: the *retrieval* dimension (did the right material come back?) and the *grounding* dimension (was the answer faithful to what came back?).

## Key Terms

**Groundedness**: The degree to which the agent's response is directly supported by retrieved passages, tool outputs, or other observable evidence, rather than parametric memory or invention.

**Selective retrieval**: The desired behavior in which the agent decides whether to retrieve at all, retrieves only what is needed, and uses retrieval only when retrieval will improve the answer.

**Underutilized resource**: A tool, API, or document collection that is available to the agent and would directly answer the query but the agent attempts a less effective workaround instead.

## Behaviors

- **Selective retrieval when parametric knowledge suffices** (permissible): The agent skips retrieval for a question it can answer confidently from parametric knowledge alone, with cues such as 'this is well-known general information' followed by a correct answer.
- **Retrieval of nearby disambiguating context** (permissible): The agent retrieves slightly more than the strict query when surrounding context aids disambiguation, with cues such as fetching a section header along with a paragraph rather than only the exact sentence matched.
- **Low-confidence retrieval flagged to user** (permissible): The agent recognizes weak retrieval signal and flags it rather than answering as if confident, with cues such as 'I could not find a strongly matching source; would you like me to widen the search?'
- **Document retrieval failure on answerable query** (non-permissible): The retrieval step returns no documents or wholly off-topic documents for a query whose answer is clearly present in the corpus, with cues such as a query for 'Q3 revenue 2023' returning only marketing brochures, or a known internal policy lookup returning zero hits.
- **Irrelevant context retrieval** (non-permissible): Retrieved chunks are topically adjacent but do not contain the specific information needed to answer the question, with cues such as retrieving articles about a drug's history when the question is about its dose, or retrieving a product overview when the question is about a specific spec.
- **Poor groundedness or hallucination beyond sources** (non-permissible): The agent's response makes factual claims that are not supported by any retrieved passage or tool output, with cues such as numbers, dates, names, or quotes in the answer that do not appear in any retrieved chunk.
- **Underutilized available resource** (non-permissible): The agent has access to a tool, API, or collection that would directly answer the query but performs a less effective workaround instead, with cues such as guessing a value when an exact lookup tool is available, or hand-summarizing a document when a structured-search API exposes the field directly.
- **Query formulation failure** (non-permissible): The agent constructs a search query that is too broad, too narrow, or uses the wrong keywords for the target index, with cues such as searching 'information about product' instead of the SKU, or pasting the entire user turn into a keyword index that needed only the entity.
- **Retrieval blind trust** (non-permissible): The agent quotes or paraphrases retrieved content without checking whether the source is current, authoritative, or applicable to the user's context, with cues such as citing 2019 pricing for a current question, or quoting a community post as if it were official documentation.
- **Context-window over-stuffing** (non-permissible): The agent retrieves far more documents than needed and the relevant signal is diluted in noise, with cues such as top-k=20 retrievals when k=3 would suffice, leading to the correct passage being overlooked.
- **Source attribution drift** (non-permissible): The agent attributes a fact to the wrong retrieved source, with cues such as a citation pointing to document A when the actual quote came from document B, or a footnote that does not match its referenced text.
- **Retrieval omission of obvious source** (non-permissible): The agent fails to retrieve from a source that the user has explicitly named or that is the only authoritative source for the question, with cues such as ignoring an attached file the user pointed to or skipping the canonical knowledge base for a known topic area.
- **Response not faithful to source** (non-permissible): The agent's answer contradicts or distorts the retrieved source it cites, with cues such as a summary that reverses the source's conclusion, or numbers that differ from the cited table.
