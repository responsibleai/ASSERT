"""Knowledge-base backend behind a clean adapter (mock-first, Foundry IQ later).

`knowledge_base_retrieve` (exposed as an MCP tool in kb_mcp_server.py) delegates
to a `KBBackend`. Two implementations:

  - MockKBBackend     : pure-python BM25 over local knowledge/*.md. No Azure.
                        Used for P0-P4 so the whole pipeline runs offline.
  - FoundryIQBackend  : real Azure AI Search "knowledge base" (agentic
                        retrieval, answerSynthesis). Used at P5 after the KB is
                        built. Swap is a single env flip: KB_BACKEND=foundry.

Both return the SAME shape so the agent + ACS controls are backend-agnostic:

    {
      "answer": str,                       # synthesized / extractive answer
      "citations": [                       # structured provenance (a FEATURE
        {"ref_id": str, "source": str,     #  the ACS 'ungrounded_policy_claim'
         "snippet": str, "score": float}   #  gate consumes)
      ],
      "grounded": bool                     # True iff supported by the corpus
    }

The `grounded` flag + `citations` presence are *typed signals* — the feature
guardrail gates on them instead of trying to pattern-match the answer text.
"""

from __future__ import annotations

import math
import os
import re
import time
from pathlib import Path
from typing import Protocol

try:
    import retrieval_trace
except ImportError:  # pragma: no cover - packaged import
    from . import retrieval_trace  # type: ignore

_TOKEN = re.compile(r"[a-z0-9]+")
_GROUNDED_SCORE_FLOOR = 3.0   # BM25 score below which we call it ungrounded
_TOP_K = 3


def _env_float(name: str, default: float | None) -> float | None:
    val = os.environ.get(name)
    if val is None or val == "":
        return default
    try:
        return float(val)
    except ValueError:
        return default
# Function words excluded from scoring/grounding so a query like "what is the
# capital of France" has no content overlap with the bank corpus -> ungrounded.
_STOPWORDS = frozenset((
    "the is a an of to for and or in on at with what how why when where who do "
    "does did can could would should shall may might must please tell show give "
    "get need want about into over under than then there here we our us they them "
    "their he she his her it its this that these those are am be been being by as "
    "from not no will i my me you your if any all".split()
))


def _tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def apply_score_threshold(citations: list[dict], threshold: float | None) -> list[dict]:
    """Drop citations below `threshold` (the precision knob). None = no filter."""
    if threshold is None:
        return citations
    return [c for c in citations if c.get("score", 0.0) >= threshold]


class KBBackend(Protocol):
    def retrieve(self, query: str) -> dict: ...


# ---------------------------------------------------------------------------
# Mock backend: pure-python BM25 over knowledge/*.md (no external deps)
# ---------------------------------------------------------------------------
class MockKBBackend:
    """Extractive BM25 retriever over a local markdown policy corpus.

    Each markdown file is split into chunks at H2/H3 headings (falling back to
    blank-line paragraphs). Each chunk gets a stable ref_id `<file>::<anchor>`.
    """

    def __init__(self, corpus_dir: str, *, grounded_floor: float | None = None,
                 top_k: int | None = None) -> None:
        self.corpus_dir = Path(corpus_dir)
        # Tunable retrieval knobs (the "KB parameters" the eval signal tunes).
        # Defaults preserve historical behavior when nothing is configured.
        self.grounded_floor = (_GROUNDED_SCORE_FLOOR if grounded_floor is None
                               else float(grounded_floor))
        self.top_k = _TOP_K if top_k is None else int(top_k)
        self.chunks: list[dict] = []          # {ref_id, source, text, tokens, len}
        self._df: dict[str, int] = {}
        self._avg_len = 0.0
        self._load()

    def _load(self) -> None:
        if not self.corpus_dir.exists():
            return
        for md in sorted(self.corpus_dir.glob("*.md")):
            # README.md documents the corpus for humans; it is not policy content.
            if md.name.lower() == "readme.md":
                continue
            self._add_file(md)
        # Document-frequency table for IDF.
        for c in self.chunks:
            for term in set(c["tokens"]):
                self._df[term] = self._df.get(term, 0) + 1
        if self.chunks:
            self._avg_len = sum(c["len"] for c in self.chunks) / len(self.chunks)

    def _add_file(self, path: Path) -> None:
        raw = path.read_text(encoding="utf-8", errors="ignore")
        # Split on H2/H3 headings; keep the heading with its body.
        parts = re.split(r"(?m)^(##+\s+.*)$", raw)
        segments: list[tuple[str, str]] = []
        if parts and parts[0].strip():           # preamble before first heading
            segments.append(("intro", parts[0].strip()))
        for i in range(1, len(parts), 2):
            heading = parts[i].lstrip("#").strip()
            body = parts[i + 1].strip() if i + 1 < len(parts) else ""
            segments.append((heading, (heading + "\n" + body).strip()))
        if not segments:                          # no headings -> paragraph split
            for j, para in enumerate(p.strip() for p in raw.split("\n\n") if p.strip()):
                segments.append((f"p{j}", para))
        for heading, text in segments:
            anchor = re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-") or "section"
            self.chunks.append({
                "ref_id": f"{path.stem}::{anchor}",
                "source": path.name,
                "text": text,
                "tokens": _tokenize(text),
                "len": max(1, len(_tokenize(text))),
            })

    def _bm25(self, q_terms: list[str], chunk: dict, k1: float = 1.5, b: float = 0.75) -> float:
        n = len(self.chunks)
        score = 0.0
        for term in q_terms:
            if term in _STOPWORDS:
                continue
            df = self._df.get(term, 0)
            # Skip terms absent from the corpus, and near-universal terms
            # (corpus-specific stopwords) so grounding hinges on content words.
            if df == 0 or df > 0.5 * n:
                continue
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            tf = chunk["tokens"].count(term)
            denom = tf + k1 * (1 - b + b * chunk["len"] / (self._avg_len or 1))
            score += idf * (tf * (k1 + 1)) / (denom or 1)
        return score

    def retrieve(self, query: str) -> dict:
        t0 = time.perf_counter()
        result = self._retrieve(query)
        retrieval_trace.emit(
            backend="mock", query=query, result=result,
            score_threshold=self.grounded_floor,
            elapsed_ms=(time.perf_counter() - t0) * 1000.0,
        )
        return result

    def _retrieve(self, query: str) -> dict:
        ungrounded = {
            "answer": "Not covered in the bank policy corpus. Route to verified bank operations.",
            "citations": [],
            "grounded": False,
        }
        q_terms = _tokenize(query)
        n = len(self.chunks) or 1
        # Grounding requires content overlap: at least one query term that is in
        # the corpus and is not a near-universal stopword. No content overlap ->
        # ungrounded, regardless of stopword score noise.
        informative = [t for t in set(q_terms)
                       if t not in _STOPWORDS and 0 < self._df.get(t, 0) <= 0.5 * n]
        if not informative:
            return ungrounded
        scored = sorted(
            ((self._bm25(q_terms, c), c) for c in self.chunks),
            key=lambda x: x[0], reverse=True,
        )[:self.top_k]
        hits = [(s, c) for s, c in scored if s > 0]
        # `grounded_floor` is the tunable retrieval knob: the verdict gates on the
        # top hit's score. Too high drops a relevant hit (recall miss -> false
        # ungrounded); too low lets a high-overlap but off-topic hit through
        # (precision miss -> false grounded). Default reproduces prior behavior.
        if not hits or hits[0][0] < self.grounded_floor:
            return ungrounded
        citations = [{
            "ref_id": c["ref_id"], "source": c["source"],
            "snippet": c["text"][:400], "score": round(s, 3),
        } for s, c in hits]
        answer = "\n\n".join(f"{c['text'][:400]} [{c['ref_id']}]" for _, c in hits)
        return {"answer": answer, "citations": citations, "grounded": True}


# ---------------------------------------------------------------------------
# Foundry IQ backend (P5) — real Azure AI Search knowledge base
# ---------------------------------------------------------------------------
class FoundryIQBackend:
    """Azure AI Search 'knowledge base' (agentic retrieval, answerSynthesis).

    Preview API `2026-05-01-preview`. Reads SEARCH_ENDPOINT / SEARCH_API_KEY /
    AZURE_SEARCH_KB_NAME. Maps the KB response to the common shape. Import is
    lazy so the mock path needs no azure-search-documents install.
    """

    def __init__(self, *, reranker_threshold: float | None = None) -> None:
        self.endpoint = os.environ["SEARCH_ENDPOINT"]
        self.kb_name = os.environ.get("AZURE_SEARCH_KB_NAME", "bank-policy-kb")
        self.api_key = os.environ["SEARCH_API_KEY"]
        # The real-KB tunable knob: drop citations whose reranker_score is below
        # this floor before deciding `grounded`. None = legacy behavior (any
        # citation counts as grounded). The retrieval-quality eval sweeps this.
        self.reranker_threshold = reranker_threshold

    def retrieve(self, query: str) -> dict:
        t0 = time.perf_counter()
        result = self._retrieve(query)
        retrieval_trace.emit(
            backend="foundry", query=query, result=result,
            score_threshold=self.reranker_threshold,
            elapsed_ms=(time.perf_counter() - t0) * 1000.0,
        )
        return result

    def _retrieve(self, query: str) -> dict:
        # Lazy import: only needed when KB_BACKEND=foundry.
        from azure.core.credentials import AzureKeyCredential
        from azure.search.documents.knowledgebases import KnowledgeBaseRetrievalClient
        from azure.search.documents.knowledgebases.models import (
            KnowledgeBaseRetrievalRequest, KnowledgeBaseMessage,
            KnowledgeBaseMessageTextContent,
        )

        client = KnowledgeBaseRetrievalClient(
            endpoint=self.endpoint, knowledge_base_name=self.kb_name,
            credential=AzureKeyCredential(self.api_key),
        )
        req = KnowledgeBaseRetrievalRequest(messages=[
            KnowledgeBaseMessage(role="user", content=[
                KnowledgeBaseMessageTextContent(text=query)])])
        result = client.retrieve(req)

        # response[*].content[*].text -> answer; references -> citations.
        answer_parts, citations = [], []
        for msg in getattr(result, "response", []) or []:
            for block in getattr(msg, "content", []) or []:
                txt = getattr(block, "text", None)
                if txt:
                    answer_parts.append(txt)
        for ref in getattr(result, "references", []) or []:
            citations.append({
                "ref_id": str(getattr(ref, "id", "")),
                "source": str(getattr(ref, "doc_key", getattr(ref, "source_data", ""))),
                "snippet": str(getattr(ref, "source_data", ""))[:400],
                "score": float(getattr(ref, "reranker_score", 0.0) or 0.0),
            })
        # Apply the tunable reranker floor (precision knob) before the verdict.
        citations = apply_score_threshold(citations, self.reranker_threshold)
        answer = "\n\n".join(answer_parts) or "Not covered in the bank policy corpus."
        return {"answer": answer, "citations": citations, "grounded": bool(citations)}


def get_backend() -> KBBackend:
    """Factory selected by KB_BACKEND env (default: mock).

    Retrieval knobs are read from env so the same process the agent/eval runs in
    can be pointed at a tuned operating point without code changes:
      - KB_RERANKER_THRESHOLD : Foundry reranker-score floor (precision knob)
      - KB_GROUNDED_FLOOR     : mock BM25 grounding floor
      - KB_TOP_K              : mock top-k
    """
    backend = os.environ.get("KB_BACKEND", "mock").lower()
    if backend == "foundry":
        return FoundryIQBackend(
            reranker_threshold=_env_float("KB_RERANKER_THRESHOLD", None),
        )
    corpus = os.environ.get(
        "KB_CORPUS_DIR",
        str(Path(__file__).parent / "knowledge"),
    )
    top_k = os.environ.get("KB_TOP_K")
    return MockKBBackend(
        corpus,
        grounded_floor=_env_float("KB_GROUNDED_FLOOR", None),
        top_k=int(top_k) if top_k else None,
    )
