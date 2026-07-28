"""Idempotent provisioner for the bank-policy Foundry IQ knowledge base.

Builds the REAL Azure AI Search backend that `FoundryIQBackend` (kb_backend.py)
reads at P5, so the grounding gate runs against an actual agentic-retrieval KB
instead of the local BM25 mock. Three resources, created in order:

  1. a vector+text search INDEX (bank-policy-index): chunks of the 7
     knowledge/*.md policy docs, each carrying a 3072-d text-embedding-3-large
     vector + an azureOpenAI vectorizer so the KB can embed the query at
     retrieval time;
  2. a knowledge SOURCE (kind=searchIndex) over that index;
  3. a Foundry IQ knowledge BASE (bank-policy-kb) bound to the source, with
     answerSynthesis (modelName from the AOAI chat deployment) enabled.

Everything is upsert-style (PUT), so re-running is safe. Config is read from
.env: SEARCH_ENDPOINT / SEARCH_API_KEY (admin), the AOAI embedding deployment
+ endpoint, AZURE_SEARCH_KB_NAME, FOUNDRY_IQ_API_VERSION.

Run:  KB_BACKEND can stay whatever; this script only writes Azure resources.
    set -a && . ./.env && set +a
    .venv-assert/bin/python examples/bank_manager_agent_control/scripts/setup_foundry_kb.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Corpus lives under the example's runtime/ package (this script is in scripts/).
KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "runtime" / "knowledge"
INDEX_NAME = "bank-policy-index"
EMBED_DIM = 3072
VECTORIZER_NAME = "aoai_text_3_large"
VECTOR_PROFILE = "hnsw_text_3_large"


# ── config ──────────────────────────────────────────────────────────────────
def _cfg() -> dict:
    endpoint = os.environ["SEARCH_ENDPOINT"].rstrip("/")
    api_version = os.environ.get("FOUNDRY_IQ_API_VERSION", "2026-05-01-preview")
    # Embeddings: prefer a dedicated AOAI endpoint if present, else ZAVA's
    # /openai/v1 base (which hosts text-embedding-3-large on this project).
    embed_dep = os.environ.get("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-large")
    zava_base = os.environ.get("ZAVA_API_BASE", "").rstrip("/")
    return {
        "endpoint": endpoint,
        "api_version": api_version,
        "kb_name": os.environ.get("AZURE_SEARCH_KB_NAME", "bank-policy-kb"),
        "embed_dep": embed_dep,
        # Embedding REST: ZAVA v1-style (Authorization: Bearer + model in body).
        "embed_v1_base": zava_base,
        # Resource URI the SEARCH service uses for the *query-time* vectorizer.
        # Derived from the v1 base host (…/openai/v1 -> https://<host>).
        "aoai_resource_uri": _aoai_resource_uri(zava_base),
        "synth_model": os.environ.get("AGENT_MODEL", "gpt-5.4-mini"),
    }


def _aoai_resource_uri(v1_base: str) -> str:
    # https://your-foundry-resource.openai.azure.com/openai/v1 -> https://your-foundry-resource.openai.azure.com
    m = re.match(r"(https://[^/]+)", v1_base or "")
    return m.group(1) if m else ""


# Secrets are read via these accessors and passed directly to request helpers —
# deliberately NOT stored in the _cfg() dict, so config values can be logged
# without ever routing a credential through a print/log sink.
def _search_key() -> str:
    return os.environ["SEARCH_API_KEY"]


def _embed_key() -> str:
    return os.environ.get("ZAVA_API_KEY", "")


def _aoai_key() -> str:
    return os.environ.get("ZAVA_API_KEY", "")


# ── REST helpers ─────────────────────────────────────────────────────────────
def _req(method: str, url: str, key: str, body: dict | None = None) -> tuple[int, object]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"api-key": key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw[:1000]


def _search_url(cfg: dict, path: str) -> str:
    return f"{cfg['endpoint']}{path}?api-version={cfg['api_version']}"


# ── embeddings (data-plane, at upload time) ─────────────────────────────────
def embed(cfg: dict, texts: list[str]) -> list[list[float]]:
    url = f"{cfg['embed_v1_base']}/embeddings"
    out: list[list[float]] = []
    for i in range(0, len(texts), 16):
        batch = texts[i:i + 16]
        body = {"input": batch, "model": cfg["embed_dep"]}
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Authorization": f"Bearer {_embed_key()}",
                     "api-key": _embed_key(), "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=90) as r:
            j = json.loads(r.read().decode())
        out.extend(d["embedding"] for d in sorted(j["data"], key=lambda d: d["index"]))
    return out


# ── chunking (mirror the mock backend's H2/H3 split for parity) ─────────────
def chunk_docs() -> list[dict]:
    chunks: list[dict] = []
    for md in sorted(KNOWLEDGE_DIR.glob("*.md")):
        if md.name.lower() == "readme.md":
            continue  # corpus documentation, not policy content (mirror kb_backend)
        raw = md.read_text(encoding="utf-8", errors="ignore")
        parts = re.split(r"(?m)^(##+\s+.*)$", raw)
        segments: list[tuple[str, str]] = []
        if parts and parts[0].strip():
            segments.append(("intro", parts[0].strip()))
        for i in range(1, len(parts), 2):
            heading = parts[i].lstrip("#").strip()
            body = parts[i + 1].strip() if i + 1 < len(parts) else ""
            segments.append((heading, (heading + "\n" + body).strip()))
        if not segments:
            for j, para in enumerate(p.strip() for p in raw.split("\n\n") if p.strip()):
                segments.append((f"p{j}", para))
        for heading, text in segments:
            anchor = re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-") or "section"
            key = re.sub(r"[^A-Za-z0-9_\-=]", "_", f"{md.stem}__{anchor}")
            chunks.append({"id": key, "source": md.name,
                           "ref_id": f"{md.stem}::{anchor}", "content": text})
    return chunks


# ── resource bodies ──────────────────────────────────────────────────────────
def index_body(cfg: dict) -> dict:
    return {
        "name": INDEX_NAME,
        "fields": [
            {"name": "id", "type": "Edm.String", "key": True, "filterable": True},
            {"name": "source", "type": "Edm.String", "filterable": True, "searchable": True},
            {"name": "ref_id", "type": "Edm.String", "filterable": True, "searchable": True},
            {"name": "content", "type": "Edm.String", "searchable": True,
             "analyzer": "standard.lucene"},
            {"name": "content_vector", "type": "Collection(Edm.Single)",
             "searchable": True, "dimensions": EMBED_DIM,
             "vectorSearchProfile": VECTOR_PROFILE},
        ],
        "vectorSearch": {
            "algorithms": [{"name": "alg", "kind": "hnsw",
                            "hnswParameters": {"metric": "cosine", "m": 4,
                                               "efConstruction": 400, "efSearch": 500}}],
            "profiles": [{"name": VECTOR_PROFILE, "algorithm": "alg",
                          "vectorizer": VECTORIZER_NAME}],
            "vectorizers": [{
                "name": VECTORIZER_NAME, "kind": "azureOpenAI",
                "azureOpenAIParameters": {
                    "resourceUri": cfg["aoai_resource_uri"],
                    "deploymentId": cfg["embed_dep"],
                    "modelName": cfg["embed_dep"],
                    "apiKey": _aoai_key() or None,
                },
            }],
        },
        "semantic": {
            "defaultConfiguration": "semantic_config",
            "configurations": [{
                "name": "semantic_config",
                "prioritizedFields": {
                    "prioritizedContentFields": [{"fieldName": "content"}],
                    "prioritizedKeywordsFields": [{"fieldName": "source"}],
                },
            }],
        },
    }


def knowledge_source_body(cfg: dict) -> dict:
    return {
        "name": f"{cfg['kb_name']}-source",
        "kind": "searchIndex",
        "description": "Bank policy/product corpus (7 markdown docs).",
        "searchIndexParameters": {
            "searchIndexName": INDEX_NAME,
            "semanticConfigurationName": "semantic_config",
            "sourceDataFields": [{"name": "ref_id"}, {"name": "source"}, {"name": "content"}],
            "searchFields": [{"name": "content"}],
        },
    }


def knowledge_base_body(cfg: dict) -> dict:
    # Schema confirmed against this resource's 2026-05-01-preview KB type:
    # outputMode in {answerSynthesis, extractiveData}; answerInstructions and
    # retrievalInstructions are top-level; knowledgeSources bind by name; a
    # `models` entry (the AOAI chat deployment) is required for synthesis.
    return {
        "name": cfg["kb_name"],
        "description": "Bank manager policy KB (Foundry IQ, answerSynthesis).",
        "outputMode": "answerSynthesis",
        "retrievalInstructions": (
            "Answer bank policy and product questions using ONLY the provided "
            "policy documents. If the documents do not cover the question, say "
            "it is not covered; do not invent policy."
        ),
        "answerInstructions": (
            "Synthesize a concise, citation-grounded answer from the retrieved "
            "policy passages. Do not state any policy not present in them."
        ),
        "knowledgeSources": [{"name": f"{cfg['kb_name']}-source"}],
        "models": [{
            "kind": "azureOpenAI",
            "azureOpenAIParameters": {
                "resourceUri": cfg["aoai_resource_uri"],
                "deploymentId": cfg["synth_model"],
                "modelName": cfg["synth_model"],
                "apiKey": _aoai_key() or None,
            },
        }],
    }


# ── steps ────────────────────────────────────────────────────────────────────
def put_index(cfg: dict) -> None:
    url = _search_url(cfg, f"/indexes/{INDEX_NAME}")
    code, body = _req("PUT", url, _search_key(), index_body(cfg))
    if code not in (200, 201, 204):
        raise RuntimeError(f"index PUT failed {code}: {json.dumps(body)[:600]}")
    print(f"[index] {INDEX_NAME} upserted ({code})")


def upload_docs(cfg: dict) -> int:
    chunks = chunk_docs()
    print(f"[embed] embedding {len(chunks)} chunks via {cfg['embed_dep']} ...")
    vectors = embed(cfg, [c["content"] for c in chunks])
    docs = [{
        "@search.action": "mergeOrUpload",
        "id": c["id"], "source": c["source"], "ref_id": c["ref_id"],
        "content": c["content"], "content_vector": v,
    } for c, v in zip(chunks, vectors)]
    url = _search_url(cfg, f"/indexes/{INDEX_NAME}/docs/index")
    code, body = _req("POST", url, _search_key(), {"value": docs})
    if code not in (200, 207):
        raise RuntimeError(f"upload failed {code}: {json.dumps(body)[:600]}")
    print(f"[upload] {len(docs)} chunks uploaded ({code})")
    return len(docs)


def put_knowledge_source(cfg: dict) -> None:
    url = _search_url(cfg, f"/knowledgeSources/{cfg['kb_name']}-source")
    code, body = _req("PUT", url, _search_key(), knowledge_source_body(cfg))
    if code not in (200, 201, 204):
        raise RuntimeError(f"knowledgeSource PUT failed {code}: {json.dumps(body)[:600]}")
    print(f"[source] {cfg['kb_name']}-source upserted ({code})")


def put_knowledge_base(cfg: dict) -> None:
    url = _search_url(cfg, f"/knowledgeBases/{cfg['kb_name']}")
    code, body = _req("PUT", url, _search_key(), knowledge_base_body(cfg))
    if code not in (200, 201, 204):
        raise RuntimeError(f"knowledgeBase PUT failed {code}: {json.dumps(body)[:600]}")
    print(f"[kb] {cfg['kb_name']} upserted ({code})")


def main() -> int:
    cfg = _cfg()
    if not cfg["embed_v1_base"] or not _embed_key():
        print("FATAL: no embedding endpoint/key (ZAVA_API_BASE / ZAVA_API_KEY).", file=sys.stderr)
        return 2
    if not cfg["aoai_resource_uri"]:
        print("FATAL: could not derive AOAI resourceUri for the vectorizer.", file=sys.stderr)
        return 2
    print(f"endpoint={cfg['endpoint']}  kb={cfg['kb_name']}  api={cfg['api_version']}")
    print(f"aoai_resource_uri={cfg['aoai_resource_uri']}  embed_dep={cfg['embed_dep']}")
    put_index(cfg)
    n = upload_docs(cfg)
    time.sleep(3)  # let the index commit before binding the source/KB.
    put_knowledge_source(cfg)
    put_knowledge_base(cfg)
    print(f"DONE: {n} chunks live in {INDEX_NAME}; KB '{cfg['kb_name']}' ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
