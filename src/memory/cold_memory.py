from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import re
from typing import Any

from .chunk_builder import build_episodic_chunks

# ── Optional heavy dependencies ───────────────────────────────────

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:  # pragma: no cover
    np = None  # type: ignore
    HAS_NUMPY = False

try:
    from sentence_transformers import SentenceTransformer
    HAS_SENTENCE_TRANSFORMERS = True
except ImportError:  # pragma: no cover
    SentenceTransformer = None  # type: ignore
    HAS_SENTENCE_TRANSFORMERS = False

try:
    import chromadb
    from chromadb.config import Settings as ChromaSettings
    HAS_CHROMADB = True
except ImportError:  # pragma: no cover
    chromadb = None  # type: ignore
    HAS_CHROMADB = False


# ── Constants ─────────────────────────────────────────────────────

MEMORY_LOCATIONS = (
    ("memory_records.json", "record"),
    (".minidevagent/memory_records.json", "record"),
    ("memory/memory_records.json", "record"),
    ("data/memory_records.json", "record"),
    ("src/memory/memory_records.json", "record"),
    (".minidevagent/episodic_summaries.json", "episodic_summary"),
    ("memory/episodic_summaries.json", "episodic_summary"),
    (".minidevagent/entity_memory.json", "entity"),
    ("memory/entity_memory.json", "entity"),
    (".minidevagent/relationship_memory.json", "relationship"),
    ("memory/relationship_memory.json", "relationship"),
)

DEFAULT_EMBEDDING_MODEL = "all-MiniLM-L6-v2"


# ── ColdMemory ────────────────────────────────────────────────────

@dataclass
class ColdMemory:
    """Persistent memory with hybrid retrieval (dense + sparse + exact + entity).

    Supports three tiers:
      Tier 1 (always): BM25 + exact match + entity match + recency scoring
      Tier 2 (numpy): TF-IDF vector cosine similarity via numpy
      Tier 3 (sentence-transformers + chromadb): Semantic embedding search
    """

    file_summaries: dict[str, str] = field(default_factory=dict)
    ast_cache: dict[str, ast.Module] = field(default_factory=dict)
    skeleton_cache: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    relationships: dict[str, list[str]] = field(default_factory=dict)
    records: list[dict[str, Any]] = field(default_factory=list)
    chunks: list[dict[str, Any]] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)

    # Vector search state (lazy-initialized)
    _embedding_model: Any = field(default=None, repr=False, init=False)
    _vector_store: Any = field(default=None, repr=False, init=False)
    _record_embeddings: Any = field(default=None, repr=False, init=False)
    _project_path: str = field(default="", repr=False)
    _embedding_dim: int = field(default=384, repr=False)

    # ── Factory ──────────────────────────────────────────────────

    @classmethod
    def from_project(cls, project_path: str) -> "ColdMemory":
        memory = cls()
        memory._project_path = project_path
        root = Path(project_path)
        for relative, default_kind in MEMORY_LOCATIONS:
            path = root / relative
            if path.is_file():
                memory.load_records(path, default_kind=default_kind)
        memory.chunks = build_episodic_chunks(memory.records)
        return memory

    # ── Data loading ─────────────────────────────────────────────

    def load_records(self, path: Path, default_kind: str = "record") -> None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self.sources.append(str(path))
        self.records.extend(self._normalise_payload(raw, str(path), default_kind))

    # ── Write-back ───────────────────────────────────────────────
    def write_record(self, text: str, kind: str = "log", **extra: Any) -> None:
        """Append a new memory record and persist to disk."""
        import datetime
        record = self._record(
            text=text, kind=kind, source="runtime",
            timestamp=datetime.datetime.now().isoformat(timespec="seconds"),
            entities=extra.pop("entities", []),
            participants=extra.pop("participants", []),
        )
        for key, value in extra.items():
            record[key] = value
        self.records.append(record)

        # Persist to .minidevagent/memory_records.json
        if self._project_path:
            store_path = Path(self._project_path) / ".minidevagent" / "memory_records.json"
            store_path.parent.mkdir(parents=True, exist_ok=True)
            existing: list[dict[str, Any]] = []
            if store_path.exists():
                try:
                    existing = json.loads(store_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    existing = []
            if not isinstance(existing, list):
                existing = []
            existing.append(record)
            store_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")

        # Invalidate embedding cache for new record
        self._record_embeddings = None

    # ── Retrieval (hybrid: dense + sparse + exact + entity) ──────

    def retrieve(
        self, query: str, kinds: set[str] | None = None, limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Hybrid retrieval with multi-strategy scoring and RRF fusion."""
        raw_candidates = [
            r for r in self.records
            if not kinds or r.get("kind", "").lower() in kinds or self._has_kind_term(r, kinds)
        ]
        chunk_candidates = [
            self._chunk_as_record(c) for c in self.chunks
            if not kinds or any(k in kinds for k in c.get("source_types", []))
            or self._has_kind_term({"text": c.get("summary", "")}, kinds)
        ]
        candidates = raw_candidates + chunk_candidates
        if not candidates:
            return []

        query_terms = self._terms(query)
        corpus_terms = [self._terms(r["text"]) for r in candidates]

        # Tier 1: Lexical scores (always available)
        exact_hits = self._exact_hits(query, query_terms, candidates)
        entity_hits = self._entity_hits(query, candidates)
        bm25_scores = self._bm25(query_terms, corpus_terms)
        recency_scores = self._recency_scores(candidates)

        # Tier 2/3: Semantic scores
        semantic_scores = self._semantic_scores(query, candidates)

        # Combine scores with RRF-like weighted sum
        ranked: list[dict[str, Any]] = []
        for idx, record in enumerate(candidates):
            exact = exact_hits.get(idx, 0.0)
            entity = entity_hits.get(idx, 0.0)
            bm25 = bm25_scores[idx]
            semantic = semantic_scores[idx]
            recency = recency_scores[idx]

            if exact + entity + bm25 + semantic <= 0:
                continue

            # Weighted fusion: exact(4x) + entity(3x) + semantic(2x) + bm25(1x) + recency(0.25x)
            score = exact * 4.0 + entity * 3.0 + semantic * 2.0 + bm25 + recency * 0.25
            if score <= 0:
                continue

            breakdown = {
                "exact_score": round(exact, 4),
                "entity_score": round(entity, 4),
                "semantic_score": round(semantic, 4),
                "bm25_score": round(bm25, 4),
                "recency_score": round(recency, 4),
            }
            methods = [n for n, v in breakdown.items() if v > 0] + ["fusion"]
            ranked.append({
                **record, "score": round(score, 4),
                "score_breakdown": breakdown, "retrieval_methods": methods,
            })

        return sorted(
            ranked, key=lambda item: (-item["score"], item.get("timestamp", ""), item["text"])
        )[:limit]

    # ── Semantic search (Tier 2 & 3) ─────────────────────────────

    def _semantic_scores(self, query: str, candidates: list[dict[str, Any]]) -> list[float]:
        """Compute semantic similarity scores.

        Tier 3: sentence-transformers embeddings (if available)
        Tier 2: numpy-based TF-IDF vectors (if numpy available)
        Tier 1 fallback: Counter-based cosine (always available)
        """
        # Tier 3: Real embeddings
        if HAS_SENTENCE_TRANSFORMERS and HAS_NUMPY:
            return self._embedding_scores(query, candidates)

        # Tier 2: Numpy TF-IDF
        if HAS_NUMPY:
            return self._numpy_tfidf_scores(query, candidates)

        # Tier 1: Counter-based cosine
        return self._counter_cosine_scores(query, candidates)

    def _embedding_scores(self, query: str, candidates: list[dict[str, Any]]) -> list[float]:
        """Tier 3: Semantic embedding similarity via sentence-transformers."""
        try:
            model = self._get_embedding_model()
            texts = [query] + [r["text"] for r in candidates]
            embeddings = model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
            query_vec = embeddings[0]
            candidate_vecs = embeddings[1:]

            # Cosine similarity
            query_norm = np.linalg.norm(query_vec)
            if query_norm == 0:
                return [0.0 for _ in candidates]

            similarities = np.dot(candidate_vecs, query_vec) / (
                np.linalg.norm(candidate_vecs, axis=1) * query_norm + 1e-10
            )
            # Normalize to [0, 1]
            similarities = (similarities + 1.0) / 2.0
            return similarities.tolist()
        except Exception:
            return self._numpy_tfidf_scores(query, candidates)

    def _numpy_tfidf_scores(self, query: str, candidates: list[dict[str, Any]]) -> list[float]:
        """Tier 2: Numpy-based TF-IDF cosine similarity."""
        try:
            all_texts = [query] + [r["text"] for r in candidates]
            all_terms = [self._terms(t) for t in all_texts]
            # Build vocabulary
            vocab = sorted(set(t for terms in all_terms for t in terms))
            if not vocab:
                return [0.0 for _ in candidates]
            term_to_idx = {t: i for i, t in enumerate(vocab)}

            # Build TF-IDF matrix
            n_docs = len(all_texts)
            vectors = np.zeros((n_docs, len(vocab)))
            for i, terms in enumerate(all_terms):
                for t in terms:
                    if t in term_to_idx:
                        vectors[i, term_to_idx[t]] += 1

            # IDF weighting
            df = np.count_nonzero(vectors, axis=0)
            idf = np.log((n_docs + 1) / (df + 1)) + 1
            vectors = vectors * idf

            # Cosine similarity
            query_vec = vectors[0]
            query_norm = np.linalg.norm(query_vec)
            if query_norm == 0:
                return [0.0 for _ in candidates]

            candidate_vecs = vectors[1:]
            norms = np.linalg.norm(candidate_vecs, axis=1)
            dots = np.dot(candidate_vecs, query_vec)
            scores = dots / (norms * query_norm + 1e-10)
            return np.maximum(scores, 0.0).tolist()
        except Exception:
            return self._counter_cosine_scores(query, candidates)

    def _counter_cosine_scores(self, query: str, candidates: list[dict[str, Any]]) -> list[float]:
        """Tier 1: Counter-based cosine similarity (always available)."""
        query_vec = Counter(self._terms(query))
        return [self._cosine(query_vec, Counter(self._terms(r["text"]))) for r in candidates]

    def _get_embedding_model(self) -> Any:
        """Lazy-load the sentence-transformers model."""
        if self._embedding_model is None and HAS_SENTENCE_TRANSFORMERS:
            model_name = os.getenv("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)
            try:
                self._embedding_model = SentenceTransformer(model_name)
            except Exception:
                pass
        return self._embedding_model

    # ── Caching (AST, summaries, skeletons) ─────────────────────

    def cache_summary(self, path: str, summary: str) -> None:
        self.file_summaries[path] = summary

    def get_summary(self, path: str) -> str | None:
        return self.file_summaries.get(path)

    def cache_ast(self, path: str, module_ast: ast.Module) -> None:
        self.ast_cache[path] = module_ast

    def get_ast(self, path: str) -> ast.Module | None:
        return self.ast_cache.get(path)

    def cache_skeleton(self, path: str, skeleton: list[dict[str, str]]) -> None:
        self.skeleton_cache[path] = skeleton

    def get_skeleton(self, path: str) -> list[dict[str, str]] | None:
        return self.skeleton_cache.get(path)

    # ── Internal helpers ─────────────────────────────────────────

    def _normalise_payload(self, raw: Any, source: str, default_kind: str) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        if isinstance(raw, list):
            return self._normalise_items(raw, default_kind, source)
        if not isinstance(raw, dict):
            return records

        for key in ("records", "memory_records", "episodic_summaries", "episodic_memory",
                     "episodes", "issues", "logs", "errors"):
            value = raw.get(key)
            if isinstance(value, list):
                records.extend(self._normalise_items(value, key.rstrip("s"), source))

        entities = raw.get("entities") or raw.get("entity_memory")
        if isinstance(entities, dict):
            for name, value in entities.items():
                text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
                records.append(self._record(f"{name}: {text}", "entity", source, entities=[str(name)]))
        elif isinstance(entities, list):
            records.extend(self._normalise_items(entities, "entity", source))

        relations = raw.get("relationships") or raw.get("relationship_memory")
        if isinstance(relations, dict):
            for name, value in relations.items():
                targets = value if isinstance(value, list) else [value]
                self.relationships[str(name)] = [str(t) for t in targets]
                records.append(self._record(f"{name} -> {', '.join(map(str, targets))}", "relationship", source, entities=[str(name)]))
        elif isinstance(relations, list):
            records.extend(self._normalise_items(relations, "relationship", source))

        if records:
            return records
        if default_kind in {"entity", "relationship"}:
            return self._normalise_items([{**{"text": json.dumps(raw, ensure_ascii=False)}, "kind": default_kind}], default_kind, source)
        if any(key in raw for key in ("text", "content", "summary", "description", "message", "resolution")):
            return self._normalise_items([raw], default_kind, source)
        return records

    def _normalise_items(self, items: list[Any], default_kind: str, source: str) -> list[dict[str, Any]]:
        result = []
        for item in items:
            if isinstance(item, str):
                result.append(self._record(item, default_kind, source))
                continue
            if not isinstance(item, dict):
                continue
            text = next(
                (str(item[k]) for k in ("text", "content", "summary", "description", "message", "resolution") if item.get(k)), ""
            )
            if not text:
                text = json.dumps(item, ensure_ascii=False)
            result.append(self._record(
                text, str(item.get("kind") or item.get("type") or default_kind), source,
                entities=item.get("entities", []),
                timestamp=str(item.get("timestamp") or item.get("time") or item.get("date") or ""),
                record_id=str(item.get("id") or ""),
                participants=item.get("participants", []),
            ))
        return result

    def _record(self, text: str, kind: str, source: str, entities: list[str] | None = None,
                timestamp: str = "", record_id: str = "", participants: list[str] | None = None) -> dict[str, Any]:
        return {
            "id": record_id, "kind": kind.lower(), "text": text.strip(),
            "entities": [str(e) for e in (entities or [])],
            "timestamp": timestamp, "source": source,
            "participants": [str(p) for p in (participants or [])],
        }

    def _chunk_as_record(self, chunk: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": chunk.get("record_id", ""), "kind": "episodic_chunk",
            "text": chunk.get("summary", ""), "entities": chunk.get("entities", []),
            "timestamp": chunk.get("time_end", ""), "source": "cold_memory_chunk",
            "participants": chunk.get("participants", []), "chunk": chunk,
        }

    def _terms(self, text: str) -> list[str]:
        ascii_terms = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text.lower())
        chinese = re.sub(r"[^一-鿿]", "", text)
        chinese_terms = [chinese[i:i + 2] for i in range(max(0, len(chinese) - 1))]
        return ascii_terms + chinese_terms

    def _exact_hits(self, query: str, terms: list[str], records: list[dict[str, Any]]) -> dict[int, float]:
        hits: dict[int, float] = {}
        phrase = query.strip().lower()
        for idx, record in enumerate(records):
            text = record["text"].lower()
            if phrase and phrase in text:
                hits[idx] = 1.0
                continue
            common = set(terms) & set(self._terms(record["text"]))
            if common:
                hits[idx] = min(1.0, len(common) / max(1, len(set(terms))))
        return hits

    def _entity_hits(self, query: str, records: list[dict[str, Any]]) -> dict[int, float]:
        identifiers = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", query.lower()))
        hits: dict[int, float] = {}
        for idx, record in enumerate(records):
            entities = {e.lower() for e in record.get("entities", [])}
            matches = identifiers & entities
            if matches:
                hits[idx] = float(len(matches))
                continue
            for entity in entities:
                if entity and entity in query.lower():
                    hits[idx] = 1.0
        return hits

    def _bm25(self, query_terms: list[str], corpus: list[list[str]]) -> list[float]:
        if not query_terms or not corpus:
            return [0.0 for _ in corpus]
        doc_count = len(corpus)
        avg_len = sum(len(d) for d in corpus) / max(1, doc_count)
        frequencies = Counter(t for t in set(query_terms) for d in corpus if t in d)
        scores = []
        for doc in corpus:
            counts = Counter(doc)
            score = 0.0
            for term in set(query_terms):
                freq = counts.get(term, 0)
                if not freq:
                    continue
                df = frequencies[term]
                idf = math.log(1 + (doc_count - df + 0.5) / (df + 0.5))
                denom = freq + 1.5 * (1 - 0.75 + 0.75 * len(doc) / max(1, avg_len))
                score += idf * (freq * 2.5 / denom)
            scores.append(score)
        return scores

    def _cosine(self, left: Counter[str], right: Counter[str]) -> float:
        if not left or not right:
            return 0.0
        dot = sum(v * right.get(t, 0) for t, v in left.items())
        mag_l = math.sqrt(sum(v * v for v in left.values()))
        mag_r = math.sqrt(sum(v * v for v in right.values()))
        return dot / (mag_l * mag_r) if mag_l and mag_r else 0.0

    def _recency_scores(self, records: list[dict[str, Any]]) -> list[float]:
        timestamps = [r.get("timestamp", "") for r in records]
        ranked = {v: i for i, v in enumerate(sorted({v for v in timestamps if v}, reverse=True))}
        if not ranked:
            return [0.0 for _ in records]
        return [1.0 / (1.0 + ranked.get(r.get("timestamp", ""), len(ranked))) for r in records]

    def _has_kind_term(self, record: dict[str, Any], kinds: set[str]) -> bool:
        text = record.get("text", "").lower()
        aliases = {
            "issue": ["issue", "bug", "问题", "缺陷"],
            "error": ["error", "报错", "错误", "exception"],
            "traceback": ["traceback", "stack trace", "堆栈"],
            "log": ["log", "日志"],
        }
        return any(alias in text for kind in kinds for alias in aliases.get(kind, [kind]))
