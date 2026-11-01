"""Embedding backends (sentence-transformers / API / TF-IDF fallback) and a FAISS-or-NumPy vector index."""
from __future__ import annotations

import logging
import re

import numpy as np

from ..config import settings

log = logging.getLogger("converge.embed")
_st_model = None


def _norm(m: np.ndarray) -> np.ndarray:
    m = np.asarray(m, dtype="float32")
    n = np.linalg.norm(m, axis=1, keepdims=True)
    n[n == 0] = 1
    return m / n


class Embedder:
    name = "base"
    semantic = True
    related = 0.55     # cosine above which two units are candidates for the same concept
    near_dup = 0.85    # cosine above which two units are almost certainly the same statement

    def fit(self, texts: list[str]) -> None: ...
    def encode(self, texts: list[str]) -> np.ndarray: raise NotImplementedError


class STEmbedder(Embedder):
    semantic = True
    related, near_dup = 0.58, 0.86

    def __init__(self) -> None:
        global _st_model
        from sentence_transformers import SentenceTransformer
        if _st_model is None:
            _st_model = SentenceTransformer(settings.embedding_model)
        self.name = f"sentence-transformers ({settings.embedding_model})"

    def encode(self, texts):
        return _norm(_st_model.encode(texts, batch_size=64, show_progress_bar=False))


class APIEmbedder(Embedder):
    semantic = True
    related, near_dup = 0.50, 0.80

    def __init__(self) -> None:
        if not (settings.embedding_api_base_url and settings.embedding_api_model):
            raise RuntimeError("EMBEDDING_API_BASE_URL and EMBEDDING_API_MODEL are required for EMBEDDING_BACKEND=api")
        self.name = f"api ({settings.embedding_api_model})"

    def encode(self, texts):
        import httpx
        out = []
        for i in range(0, len(texts), 64):
            r = httpx.post(settings.embedding_api_base_url + "/embeddings",
                           headers={"Authorization": f"Bearer {settings.embedding_api_key}"},
                           json={"model": settings.embedding_api_model, "input": texts[i:i + 64]}, timeout=120)
            if r.status_code != 200:
                raise RuntimeError(f"Embedding API error HTTP {r.status_code}: {r.text[:200]}")
            out += [d["embedding"] for d in sorted(r.json()["data"], key=lambda d: d["index"])]
        return _norm(np.array(out))


def _analyzer(text: str) -> list[str]:
    from .textutil import STOP, stem
    raw = re.findall(r"[a-z]+|\d+(?:[.,]\d+)*", text.lower())
    toks = [w.rstrip(".,") if w[0].isdigit() else stem(w) for w in raw if w[0].isdigit() or (w not in STOP and len(w) > 2)]
    return toks + [a + "_" + b for a, b in zip(toks, toks[1:])]


class TfidfEmbedder(Embedder):
    """Lexical fallback. Works offline; weaker on paraphrases, so thresholds are lower."""
    semantic = False
    related, near_dup = 0.28, 0.60
    name = "TF-IDF (lexical fallback)"

    def __init__(self) -> None:
        from sklearn.feature_extraction.text import TfidfVectorizer
        self.vec = TfidfVectorizer(sublinear_tf=True, analyzer=_analyzer, min_df=1, max_features=8000)

    def fit(self, texts):
        self.vec.fit(texts)

    def encode(self, texts):
        return _norm(self.vec.transform(texts).toarray())


def get_embedder(texts: list[str]) -> tuple[Embedder, str | None]:
    """Returns (embedder, note). `note` explains a fallback so the UI can say so honestly."""
    b = settings.embedding_backend
    note = None
    emb: Embedder | None = None
    if b in ("auto", "sentence-transformers"):
        try:
            emb = STEmbedder()
        except Exception as e:
            if b == "sentence-transformers":
                raise RuntimeError(f"EMBEDDING_BACKEND=sentence-transformers but it is unavailable: {e}")
            note = ("Semantic embeddings unavailable (install `sentence-transformers` and allow the model "
                    "download); using the lexical TF-IDF fallback, which is weaker at matching paraphrases.")
    elif b == "api":
        emb = APIEmbedder()
    if emb is None:
        emb = TfidfEmbedder()
        note = note or "EMBEDDING_BACKEND=tfidf: using the lexical TF-IDF fallback."
    emb.fit(texts)
    return emb, note


class VectorIndex:
    def __init__(self, vecs: np.ndarray):
        self.vecs = np.ascontiguousarray(vecs, dtype="float32")
        self.faiss = None
        try:
            import faiss
            self.faiss = faiss.IndexFlatIP(self.vecs.shape[1])
            self.faiss.add(self.vecs)
        except Exception:
            self.faiss = None

    @property
    def backend(self) -> str:
        return "FAISS" if self.faiss is not None else "NumPy"

    def search(self, q: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
        k = max(1, min(k, len(self.vecs)))
        q = np.ascontiguousarray(q, dtype="float32")
        if self.faiss is not None:
            return self.faiss.search(q, k)
        sims = q @ self.vecs.T
        idx = np.argsort(-sims, axis=1)[:, :k]
        return np.take_along_axis(sims, idx, axis=1), idx
