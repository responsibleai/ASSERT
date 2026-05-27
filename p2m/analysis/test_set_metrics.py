# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from p2m.core.io import load_jsonl, row_behavior

try:
    import numpy as np
except ModuleNotFoundError:
    np = None

try:
    from openai import OpenAI
except ModuleNotFoundError:
    OpenAI = None

# Optional local embedding backend
try:
    from sentence_transformers import SentenceTransformer
except Exception:  # pragma: no cover - optional dependency
    SentenceTransformer = None

# ---------------------- Helpers ----------------------


@dataclass
class Config:
    taxonomy_path: str
    test_set_path: str
    embed_model: str
    embed_backend: str  # "openai" or "hf"
    k_list: List[int]
    example_distance_thresh: float
    presence_coverage: bool
    out_json: str
    out_md: Optional[str]


NEAR_DUP_THRESHOLD = 0.95


def _missing_dependency(name: str) -> ModuleNotFoundError:
    exc = ModuleNotFoundError(f"No module named '{name}'")
    exc.name = name
    return exc


def _require_numpy() -> Any:
    if np is None:
        raise _missing_dependency("numpy")
    return np


def _require_openai() -> Any:
    if OpenAI is None:
        raise _missing_dependency("openai")
    return OpenAI


def load_policy(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    behavior_categories = data.get("behavior_categories") or []
    flattened = []
    for idx, sr in enumerate(behavior_categories):
        flattened.append(
            {
                "id": idx,
                "name": sr.get("name", f"behavior_{idx}"),
                "definition": sr.get("definition", ""),
                "examples": sr.get("examples", []) or [],
            }
        )
    return {"raw": data, "behavior_categories": flattened}


def _prompt_test_case_text(row: Dict[str, Any]) -> str | None:
    if row.get("type") != "prompt":
        return None
    row_test_case = row.get("seed")
    if isinstance(row_test_case, dict):
        text = str(row_test_case.get("description") or "").strip()
        return text or None
    return None


def pairwise_sims(mat: np.ndarray) -> np.ndarray:
    np_mod = _require_numpy()
    if mat.shape[0] < 2:
        return np_mod.array([])
    norm = mat / np_mod.linalg.norm(mat, axis=1, keepdims=True)
    sim = norm @ norm.T
    # extract upper triangle without diagonal
    iu = np_mod.triu_indices_from(sim, k=1)
    return sim[iu]


def vendi_score(mat: np.ndarray) -> float:
    """Effective set size via entropy of similarity matrix eigenvalues."""
    np_mod = _require_numpy()
    n = mat.shape[0]
    if n == 0:
        return 0.0
    # similarity matrix using cosine
    norm = mat / np_mod.linalg.norm(mat, axis=1, keepdims=True)
    k = norm @ norm.T
    eigvals = np_mod.linalg.eigvalsh(k / n)
    eigvals = np_mod.clip(eigvals, 1e-12, None)
    ent = -np_mod.sum(eigvals * np_mod.log(eigvals))
    return float(np_mod.exp(ent))


def gini(arr: List[float]) -> float:
    np_mod = _require_numpy()
    if not arr:
        return 0.0
    x = np_mod.array(arr)
    if np_mod.allclose(x, 0):
        return 0.0
    x_sorted = np_mod.sort(x)
    n = len(x)
    idx = np_mod.arange(1, n + 1)
    return float((2 * np_mod.sum(idx * x_sorted) / (n * np_mod.sum(x_sorted))) - (n + 1) / n)


def entropy(arr: List[float]) -> float:
    np_mod = _require_numpy()
    if not arr:
        return 0.0
    x = np_mod.array(arr, dtype=float)
    total = x.sum()
    if total <= 0:
        return 0.0
    p = x / total
    p = p[p > 0]
    return float(-np_mod.sum(p * np_mod.log(p)))


def embed_texts_openai(client: OpenAI, model: str, texts: List[str]) -> np.ndarray:
    np_mod = _require_numpy()
    if not texts:
        return np_mod.zeros((0, 1))
    resp = client.embeddings.create(model=model, input=texts)
    vecs = [np_mod.array(e.embedding, dtype=float) for e in resp.data]
    return np_mod.vstack(vecs)


def embed_texts_hf(model: str, texts: List[str]) -> np.ndarray:
    np_mod = _require_numpy()
    if not texts:
        return np_mod.zeros((0, 1))
    if SentenceTransformer is None:
        raise RuntimeError(
            "sentence_transformers not available; install it or use --embed-backend openai"
        )
    st_model = SentenceTransformer(model)
    vecs = st_model.encode(
        texts,
        convert_to_numpy=True,
        show_progress_bar=False,
        normalize_embeddings=False,
    )
    return vecs


def embed_texts(
    client: OpenAI, backend: str, model: str, texts: List[str]
) -> np.ndarray:
    if backend == "hf":
        return embed_texts_hf(model, texts)
    return embed_texts_openai(client, model, texts)


def coverage_at_k(behavior_category_counts: Dict[str, int], k: int, total_behavior_categories: int) -> float:
    if total_behavior_categories == 0:
        return 0.0
    hits = sum(1 for c in behavior_category_counts.values() if c >= k)
    return hits / total_behavior_categories


# ---------------------- Main logic ----------------------


def compute_metrics(cfg: Config):
    np_mod = _require_numpy()
    tax = load_policy(Path(cfg.taxonomy_path))
    behavior_categories = tax["behavior_categories"]
    behavior_names = [sr["name"] for sr in behavior_categories]

    test_set = load_jsonl(Path(cfg.test_set_path))

    client = _require_openai()() if cfg.embed_backend == "openai" else None

    # Embed taxonomy examples for coverage calc
    example_texts = []
    for sr in behavior_categories:
        for ex in sr.get("examples", []):
            example_texts.append(ex)
    example_vecs = embed_texts(
        client, cfg.embed_backend, cfg.embed_model, example_texts
    )

    # Collect test-case info
    per_behavior_vecs: Dict[str, List[np.ndarray]] = defaultdict(list)
    per_behavior_valid: Dict[str, List[bool]] = defaultdict(list)
    per_behavior_len: Dict[str, List[int]] = defaultdict(list)

    prompt_test_cases = [entry for entry in test_set if _prompt_test_case_text(entry) is not None]
    test_case_prompts = [_prompt_test_case_text(entry) or "" for entry in prompt_test_cases]
    test_case_vecs = embed_texts(client, cfg.embed_backend, cfg.embed_model, test_case_prompts)

    def embed_dim() -> int:
        if test_case_vecs.size:
            return test_case_vecs.shape[1]
        if example_vecs.size:
            return example_vecs.shape[1]
        return 1

    unknown_behavior_category_counts: Dict[str, int] = defaultdict(int)
    for entry, vec in zip(prompt_test_cases, test_case_vecs):
        sr = row_behavior(entry)
        if sr not in behavior_names:
            unknown_behavior_category_counts[sr] += 1
            continue
        per_behavior_vecs[sr].append(vec)
        per_behavior_valid[sr].append(True)
        per_behavior_len[sr].append(len(_prompt_test_case_text(entry) or ""))

    if unknown_behavior_category_counts:
        summary = ", ".join(
            f"{name} ({count})" for name, count in sorted(unknown_behavior_category_counts.items())
        )
        print(
            f"[test_set_metrics] Skipping {sum(unknown_behavior_category_counts.values())} test_set "
            f"with behavior_categories not in taxonomy.json: {summary}"
        )

    # Metrics per behavior
    per_stats = {}
    for sr in behavior_names:
        vecs = per_behavior_vecs.get(sr, [])
        valids = per_behavior_valid.get(sr, [])
        lens = per_behavior_len.get(sr, [])
        valid_vecs = [v for v, ok in zip(vecs, valids) if ok]
        valid_mat = np_mod.vstack(valid_vecs) if valid_vecs else np_mod.zeros((0, embed_dim()))

        # coverage count
        valid_count = len(valid_vecs)
        total = len(vecs)
        valid_rate = valid_count / total if total else 0.0

        # near-dup rate
        sims = pairwise_sims(valid_mat)
        near_dup_rate = float(np_mod.mean(sims > NEAR_DUP_THRESHOLD)) if sims.size else 0.0

        # vendi
        vendi = vendi_score(valid_mat) if valid_count > 0 else 0.0
        vendi_norm = vendi / valid_count if valid_count > 0 else 0.0

        # length stats
        mean_len = statistics.fmean(lens) if lens else 0.0
        var_len = statistics.pvariance(lens) if len(lens) > 1 else 0.0

        per_stats[sr] = {
            "valid_count": valid_count,
            "total_count": total,
            "valid_rate": valid_rate,
            "near_dup_rate": near_dup_rate,
            "vendi": vendi,
            "vendi_norm": vendi_norm,
            "mean_len": mean_len,
            "var_len": var_len,
        }

    # Overall coverage
    counts = {sr: per_stats[sr]["valid_count"] for sr in behavior_names}
    if cfg.presence_coverage:
        covered = sum(1 for c in counts.values() if c > 0)
        coverage = {"presence": covered / len(behavior_names) if behavior_names else 0.0}
    else:
        coverage = {k: coverage_at_k(counts, k, len(behavior_names)) for k in cfg.k_list}

    # Skew
    count_vals = list(counts.values())
    skew_gini = gini(count_vals)
    skew_ent = entropy(count_vals)

    # Example coverage
    ex_cov = {"fraction_within": 0.0, "median_distance": None}
    if example_vecs.shape[0] > 0:
        valid_all_list = [
            vec
            for sr in behavior_names
            for vec, ok in zip(
                per_behavior_vecs.get(sr, []), per_behavior_valid.get(sr, [])
            )
            if ok
        ]
        if valid_all_list:
            valid_all = np_mod.vstack(valid_all_list)
            # compute min distance per example
            norm_valid = valid_all / np_mod.linalg.norm(valid_all, axis=1, keepdims=True)
            norm_examples = example_vecs / np_mod.linalg.norm(
                example_vecs, axis=1, keepdims=True
            )
            sims = norm_examples @ norm_valid.T
            max_sims = sims.max(axis=1)
            within = (1 - max_sims) <= cfg.example_distance_thresh  # cosine distance
            ex_cov["fraction_within"] = float(np_mod.mean(within))
            ex_cov["median_distance"] = float(np_mod.median(1 - max_sims))
        else:
            ex_cov["fraction_within"] = 0.0
            ex_cov["median_distance"] = None

    overall = {
        "coverage": coverage,
        "skew": {"gini": skew_gini, "entropy": skew_ent},
        "example_coverage": ex_cov,
        "valid_total": sum(count_vals),
        "test_case_total": len(test_set),
    }

    report = {
        "config": asdict(cfg),
        "overall": overall,
        "per_behavior": per_stats,
    }

    Path(cfg.out_json).parent.mkdir(parents=True, exist_ok=True)
    Path(cfg.out_json).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if cfg.out_md:
        lines = ["# Test Set Metrics", "", "## Overall", ""]
        if cfg.presence_coverage:
            lines.append(f"Coverage: presence={coverage['presence']:.3f}")
        else:
            lines.append(
                "Coverage: "
                + ", ".join(f"@{k}={coverage[k]:.3f}" for k in cfg.k_list)
            )
        lines.append(f"Valid total: {overall['valid_total']} / {overall['test_case_total']}")
        lines.append(f"Gini: {skew_gini:.3f}, Entropy: {skew_ent:.3f}")
        if ex_cov["median_distance"] is not None:
            lines.append(
                f"Example coverage: {ex_cov['fraction_within']:.3f}, median dist={ex_cov['median_distance']:.3f}"
            )
        else:
            lines.append("Example coverage: n/a")
        lines.append("")
        lines.append("## Per behavior")
        lines.append("")
        lines.append(
            "| behavior | valid | total | valid_rate | vendi | vendi/n | near_dup | mean_len | var_len |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for sr in behavior_names:
            ps = per_stats[sr]
            lines.append(
                f"| {sr} | {ps['valid_count']} | {ps['total_count']} | {ps['valid_rate']:.3f} | "
                f"{ps['vendi']:.3f} | {ps['vendi_norm']:.3f} | {ps['near_dup_rate']:.3f} | "
                f"{ps['mean_len']:.1f} | {ps['var_len']:.1f} |"
            )
        Path(cfg.out_md).write_text("\n".join(lines), encoding="utf-8")
