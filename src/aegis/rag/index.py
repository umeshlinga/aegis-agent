"""Hybrid retrieval: BM25 + dense embeddings, fused with Reciprocal Rank Fusion,
then reranked (cross-encoder or LLM), with every chunk traceable to its source.

Why hybrid: variant evidence is full of exact tokens (R1699W, c.5095C>T,
rs numbers, gene symbols) that dense embeddings blur together, and of
paraphrase ("loss of function", "abolished activity") that BM25 misses.
RRF combines both rank lists without having to calibrate their scores.

Embedders, best first (whichever is installed/configured is used, and the
name is recorded in every result so evals know what they measured):
  1. fastembed  BAAI/bge-small-en-v1.5  (ONNX, local, no GPU needed)
  2. OpenAI     text-embedding-3-small  (if OPENAI_API_KEY and AEGIS_EMBEDDER=openai)
  3. hashing    signed feature hashing  (dependency-free fallback; weak, for tests)
Rerankers: fastembed cross-encoder ms-marco-MiniLM-L-6-v2, or an LLM judge.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import threading
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import numpy as np

TOKEN = re.compile(r"[A-Za-z0-9]+(?:[.>:_*-][A-Za-z0-9]+)*")
STOP = set("the a an and or of to in for on with by is are was were be been this that these those as at from "
           "it its we our their which who whom has have had not no but into than then there".split())


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in TOKEN.findall(text) if t.lower() not in STOP]


def chunk(text: str, max_words: int = 160, overlap: int = 30) -> list[str]:
    words = text.split()
    if len(words) <= max_words:
        return [text] if text.strip() else []
    out, i = [], 0
    while i < len(words):
        out.append(" ".join(words[i:i + max_words]))
        i += max_words - overlap
    return out


# ------------------------------------------------------------------ embedders
class HashingEmbedder:
    name = "hashing-512"

    def __init__(self, dim: int = 512):
        self.dim = dim

    def embed(self, texts: list[str]) -> np.ndarray:
        m = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            toks = tokenize(t)
            grams = toks + [a + "_" + b for a, b in zip(toks, toks[1:])]
            for g in grams:
                h = int.from_bytes(hashlib.md5(g.encode()).digest()[:8], "little")
                m[i, h % self.dim] += 1.0 if (h >> 63) & 1 else -1.0
        n = np.linalg.norm(m, axis=1, keepdims=True)
        return m / np.maximum(n, 1e-9)


class FastEmbedEmbedder:
    def __init__(self, model: str = "BAAI/bge-small-en-v1.5"):
        from fastembed import TextEmbedding

        self.model = TextEmbedding(model_name=model)
        self.name = f"fastembed:{model}"

    def embed(self, texts: list[str]) -> np.ndarray:
        m = np.array(list(self.model.embed(texts)), dtype=np.float32)
        return m / np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-9)


class OpenAIEmbedder:
    def __init__(self, api_key: str, model: str = "text-embedding-3-small"):
        import openai

        self.client = openai.OpenAI(api_key=api_key)
        self.model = model
        self.name = f"openai:{model}"

    def embed(self, texts: list[str]) -> np.ndarray:
        r = self.client.embeddings.create(model=self.model, input=texts)
        m = np.array([d.embedding for d in r.data], dtype=np.float32)
        return m / np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-9)


def make_embedder(kind: str = "auto", openai_key: str | None = None):
    if kind in ("auto", "fastembed"):
        try:
            return FastEmbedEmbedder()
        except Exception:
            if kind == "fastembed":
                raise
    if kind == "openai" and openai_key:
        return OpenAIEmbedder(openai_key)
    return HashingEmbedder()


class CrossEncoderReranker:
    def __init__(self, model: str = "Xenova/ms-marco-MiniLM-L-6-v2"):
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        self.model = TextCrossEncoder(model_name=model)
        self.name = f"cross-encoder:{model}"

    def scores(self, query: str, docs: list[str]) -> list[float]:
        return [float(s) for s in self.model.rerank(query, docs)]


def make_reranker(kind: str = "auto"):
    if kind in ("auto", "cross_encoder"):
        try:
            return CrossEncoderReranker()
        except Exception:
            if kind == "cross_encoder":
                raise
    return None


# ---------------------------------------------------------------------- BM25
class BM25:
    def __init__(self, docs_tokens: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.docs = docs_tokens
        self.N = len(docs_tokens)
        self.avgdl = sum(map(len, docs_tokens)) / max(self.N, 1)
        df: Counter = Counter()
        for d in docs_tokens:
            df.update(set(d))
        self.idf = {t: math.log(1 + (self.N - n + 0.5) / (n + 0.5)) for t, n in df.items()}
        self.tf = [Counter(d) for d in docs_tokens]

    def scores(self, q: list[str]) -> np.ndarray:
        s = np.zeros(self.N, dtype=np.float32)
        for i, tf in enumerate(self.tf):
            dl = len(self.docs[i])
            for t in q:
                if t in tf:
                    f = tf[t]
                    s[i] += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
        return s


def rrf(rank_lists: list[list[int]], k: int = 60) -> dict[int, float]:
    fused: dict[int, float] = {}
    for ranks in rank_lists:
        for r, idx in enumerate(ranks):
            fused[idx] = fused.get(idx, 0.0) + 1.0 / (k + r + 1)
    return fused


# ---------------------------------------------------------------------- index
@dataclass
class Hit:
    doc_id: str
    chunk_id: int
    text: str
    meta: dict
    score: float
    bm25_rank: int | None = None
    dense_rank: int | None = None
    rerank_score: float | None = None


@dataclass
class RetrievalResult:
    query: str
    hits: list[Hit]
    embedder: str
    reranker: str | None
    stats: dict = field(default_factory=dict)


class HybridIndex:
    """In-memory hybrid index with optional SQLite persistence (for agent memory)."""

    def __init__(self, embedder=None, reranker=None, db_path: str | None = None, collection: str = "default"):
        self.embedder = embedder or HashingEmbedder()
        self.reranker = reranker
        self.collection = collection
        self.items: list[tuple[str, int, str, dict]] = []
        self.vectors: np.ndarray | None = None
        self._bm25: BM25 | None = None
        self._lock = threading.Lock()
        self.db = None
        if db_path:
            self.db = sqlite3.connect(db_path, check_same_thread=False)
            self.db.execute("""CREATE TABLE IF NOT EXISTS rag_docs (collection TEXT, doc_id TEXT, chunk_id INTEGER,
                               text TEXT, meta_json TEXT, PRIMARY KEY(collection, doc_id, chunk_id))""")
            rows = self.db.execute("SELECT doc_id, chunk_id, text, meta_json FROM rag_docs WHERE collection=?",
                                   (collection,)).fetchall()
            if rows:
                self._add_items([(d, c, t, json.loads(m)) for d, c, t, m in rows], persist=False)

    def __len__(self) -> int:
        return len(self.items)

    def add(self, doc_id: str, text: str, meta: dict | None = None, max_words: int = 160) -> int:
        chunks = chunk(text, max_words)
        self._add_items([(doc_id, i, c, dict(meta or {})) for i, c in enumerate(chunks)])
        return len(chunks)

    def _add_items(self, items: list[tuple[str, int, str, dict]], persist: bool = True) -> None:
        if not items:
            return
        vecs = self.embedder.embed([t for _, _, t, _ in items])
        with self._lock:
            self.items.extend(items)
            self.vectors = vecs if self.vectors is None else np.vstack([self.vectors, vecs])
            self._bm25 = None
        if persist and self.db is not None:
            self.db.executemany("INSERT OR REPLACE INTO rag_docs VALUES (?,?,?,?,?)",
                                [(self.collection, d, c, t, json.dumps(m)) for d, c, t, m in items])
            self.db.commit()

    def search(self, query: str, k: int = 6, candidates: int = 30, filters: dict | None = None) -> RetrievalResult:
        if not self.items:
            return RetrievalResult(query, [], self.embedder.name, getattr(self.reranker, "name", None))
        with self._lock:
            if self._bm25 is None:
                self._bm25 = BM25([tokenize(t) for _, _, t, _ in self.items])
            bm, vecs, items = self._bm25, self.vectors, list(self.items)
        allowed = [i for i, it in enumerate(items) if not filters or all(it[3].get(fk) == fv for fk, fv in filters.items())]
        if not allowed:
            return RetrievalResult(query, [], self.embedder.name, getattr(self.reranker, "name", None))
        bscore = bm.scores(tokenize(query))
        qv = self.embedder.embed([query])[0]
        dscore = vecs @ qv
        bm_rank = sorted(allowed, key=lambda i: -bscore[i])[:candidates]
        bm_rank = [i for i in bm_rank if bscore[i] > 0]
        de_rank = sorted(allowed, key=lambda i: -dscore[i])[:candidates]
        fused = rrf([bm_rank, de_rank])
        order = sorted(fused, key=lambda i: -fused[i])[:candidates]
        hits = [Hit(items[i][0], items[i][1], items[i][2], items[i][3], fused[i],
                    bm_rank.index(i) if i in bm_rank else None, de_rank.index(i) if i in de_rank else None)
                for i in order]
        if self.reranker is not None and hits:
            rs = self.reranker.scores(query, [h.text for h in hits])
            for h, s in zip(hits, rs):
                h.rerank_score = s
            hits.sort(key=lambda h: -(h.rerank_score or 0))
        return RetrievalResult(query, hits[:k], self.embedder.name, getattr(self.reranker, "name", None),
                               {"candidates": len(order), "bm25_nonzero": len(bm_rank)})


async def llm_rerank(router, query: str, hits: list[Hit], keep: int = 5) -> list[Hit]:
    """LLM listwise rerank: the model scores relevance 0-3 for each chunk."""
    from pydantic import BaseModel

    from ..llm.base import LLMRequest

    class Scores(BaseModel):
        scores: list[int]

    if not hits:
        return hits
    listing = "\n\n".join(f"[{i}] {h.text[:700]}" for i, h in enumerate(hits))
    req = LLMRequest(system="Score how useful each passage is for answering the query: 0 none, 1 weak, 2 useful, "
                            "3 directly answers. Return one integer per passage, in order.",
                     messages=[{"role": "user", "content": f"QUERY: {query}\n\nPASSAGES:\n{listing}"}],
                     tier="fast", purpose="rag_rerank", max_tokens=300)
    s, _ = await router.structured(req, Scores)
    for h, sc in zip(hits, s.scores + [0] * len(hits)):
        h.rerank_score = float(sc)
    return sorted(hits, key=lambda h: -(h.rerank_score or 0))[:keep]
