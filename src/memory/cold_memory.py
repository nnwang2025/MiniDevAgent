from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass, field
import json
import math
from pathlib import Path
import re
from typing import Any

from .chunk_builder import build_episodic_chunks


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


@dataclass
class ColdMemory:
    file_summaries: dict[str, str] = field(default_factory=dict)
    ast_cache: dict[str, ast.Module] = field(default_factory=dict)
    skeleton_cache: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    relationships: dict[str, list[str]] = field(default_factory=dict)
    records: list[dict[str, Any]] = field(default_factory=list)
    chunks: list[dict[str, Any]] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)

    @classmethod
    def from_project(cls, project_path: str) -> "ColdMemory":
        memory = cls()
        root = Path(project_path)
        for relative, default_kind in MEMORY_LOCATIONS:
            path = root / relative
            if path.is_file():
                memory.load_records(path, default_kind=default_kind)
        memory.chunks = build_episodic_chunks(memory.records)
        return memory

    def load_records(self, path: Path, default_kind: str = "record") -> None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self.sources.append(str(path))
        self.records.extend(self._normalise_payload(raw, str(path), default_kind))

    def retrieve(self, query: str, kinds: set[str] | None = None, limit: int = 5) -> list[dict[str, Any]]:
        raw_candidates = [
            record for record in self.records
            if not kinds or record.get("kind", "").lower() in kinds or self._has_kind_term(record, kinds)
        ]
        chunk_candidates = [
            self._chunk_as_record(chunk)
            for chunk in self.chunks
            if not kinds or any(kind in kinds for kind in chunk.get("source_types", [])) or self._has_kind_term({"text": chunk.get("summary", "")}, kinds)
        ]
        candidates = raw_candidates + chunk_candidates
        if not candidates:
            return []

        query_terms = self._terms(query)
        corpus_terms = [self._terms(record["text"]) for record in candidates]
        entity_hits = self._entity_hits(query, candidates)
        exact_hits = self._exact_hits(query, query_terms, candidates)
        bm25_scores = self._bm25(query_terms, corpus_terms)
        vector_scores = [self._cosine(self._vector(query), self._vector(record["text"])) for record in candidates]
        recency_scores = self._recency_scores(candidates)

        ranked: list[dict[str, Any]] = []
        for idx, record in enumerate(candidates):
            exact = exact_hits.get(idx, 0.0)
            entity = entity_hits.get(idx, 0.0)
            bm25 = bm25_scores[idx]
            vector = vector_scores[idx]
            recency = recency_scores[idx]
            if exact + entity + bm25 + vector <= 0:
                continue
            score = exact * 4.0 + entity * 3.0 + bm25 + vector + recency * 0.25
            if score <= 0:
                continue
            breakdown = {
                "exact_score": round(exact, 4),
                "entity_score": round(entity, 4),
                "bm25_score": round(bm25, 4),
                "vector_score": round(vector, 4),
                "recency_score": round(recency, 4),
            }
            methods = [name for name, value in breakdown.items() if value > 0] + ["rerank"]
            ranked.append({**record, "score": round(score, 4), "score_breakdown": breakdown, "retrieval_methods": methods})
        return sorted(ranked, key=lambda item: (-item["score"], item.get("timestamp", ""), item["text"]))[:limit]

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

    def _normalise_payload(self, raw: Any, source: str, default_kind: str) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        if isinstance(raw, list):
            return self._normalise_items(raw, default_kind, source)
        if not isinstance(raw, dict):
            return records

        for key in ("records", "memory_records", "episodic_summaries", "episodic_memory", "episodes", "issues", "logs", "errors"):
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
                self.relationships[str(name)] = [str(target) for target in targets]
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
            text = next((str(item[key]) for key in ("text", "content", "summary", "description", "message", "resolution") if item.get(key)), "")
            if not text:
                text = json.dumps(item, ensure_ascii=False)
            result.append(
                self._record(
                    text,
                    str(item.get("kind") or item.get("type") or default_kind),
                    source,
                    entities=item.get("entities", []),
                    timestamp=str(item.get("timestamp") or item.get("time") or item.get("date") or ""),
                    record_id=str(item.get("id") or ""),
                    participants=item.get("participants", []),
                )
            )
        return result

    def _record(
        self,
        text: str,
        kind: str,
        source: str,
        entities: list[str] | None = None,
        timestamp: str = "",
        record_id: str = "",
        participants: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "id": record_id,
            "kind": kind.lower(),
            "text": text.strip(),
            "entities": [str(entity) for entity in (entities or [])],
            "timestamp": timestamp,
            "source": source,
            "participants": [str(participant) for participant in (participants or [])],
        }

    def _chunk_as_record(self, chunk: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": chunk.get("record_id", ""),
            "kind": "episodic_chunk",
            "text": chunk.get("summary", ""),
            "entities": chunk.get("entities", []),
            "timestamp": chunk.get("time_end", ""),
            "source": "cold_memory_chunk",
            "participants": chunk.get("participants", []),
            "chunk": chunk,
        }

    def _terms(self, text: str) -> list[str]:
        ascii_terms = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text.lower())
        chinese = re.sub(r"[^\u4e00-\u9fff]", "", text)
        chinese_terms = [chinese[i:i + 2] for i in range(max(0, len(chinese) - 1))]
        return ascii_terms + chinese_terms

    def _exact_hits(self, query: str, terms: list[str], records: list[dict[str, Any]]) -> dict[int, float]:
        hits: dict[int, float] = {}
        phrase = query.strip().lower()
        for index, record in enumerate(records):
            text = record["text"].lower()
            if phrase and phrase in text:
                hits[index] = 1.0
                continue
            common = set(terms) & set(self._terms(record["text"]))
            if common:
                hits[index] = min(1.0, len(common) / max(1, len(set(terms))))
        return hits

    def _entity_hits(self, query: str, records: list[dict[str, Any]]) -> dict[int, float]:
        identifiers = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", query.lower()))
        hits: dict[int, float] = {}
        for index, record in enumerate(records):
            entities = {entity.lower() for entity in record.get("entities", [])}
            matches = identifiers & entities
            if matches:
                hits[index] = float(len(matches))
                continue
            for entity in entities:
                if entity and entity in query.lower():
                    hits[index] = 1.0
        return hits

    def _bm25(self, query_terms: list[str], corpus: list[list[str]]) -> list[float]:
        if not query_terms or not corpus:
            return [0.0 for _ in corpus]
        document_count = len(corpus)
        avg_len = sum(len(document) for document in corpus) / max(1, document_count)
        frequencies = Counter(term for term in set(query_terms) for document in corpus if term in document)
        scores = []
        for document in corpus:
            counts = Counter(document)
            score = 0.0
            for term in set(query_terms):
                freq = counts.get(term, 0)
                if not freq:
                    continue
                df = frequencies[term]
                idf = math.log(1 + (document_count - df + 0.5) / (df + 0.5))
                denom = freq + 1.5 * (1 - 0.75 + 0.75 * len(document) / max(1, avg_len))
                score += idf * (freq * 2.5 / denom)
            scores.append(score)
        return scores

    def _vector(self, text: str) -> Counter[str]:
        return Counter(self._terms(text))

    def _cosine(self, left: Counter[str], right: Counter[str]) -> float:
        if not left or not right:
            return 0.0
        dot = sum(value * right.get(term, 0) for term, value in left.items())
        magnitude_left = math.sqrt(sum(value * value for value in left.values()))
        magnitude_right = math.sqrt(sum(value * value for value in right.values()))
        return dot / (magnitude_left * magnitude_right) if magnitude_left and magnitude_right else 0.0

    def _recency_scores(self, records: list[dict[str, Any]]) -> list[float]:
        timestamps = [record.get("timestamp", "") for record in records]
        ranked = {value: index for index, value in enumerate(sorted({value for value in timestamps if value}, reverse=True))}
        if not ranked:
            return [0.0 for _ in records]
        return [1.0 / (1.0 + ranked.get(record.get("timestamp", ""), len(ranked))) for record in records]

    def _has_kind_term(self, record: dict[str, Any], kinds: set[str]) -> bool:
        text = record.get("text", "").lower()
        aliases = {
            "issue": ["issue", "bug", "问题", "缺陷"],
            "error": ["error", "报错", "错误", "exception"],
            "traceback": ["traceback", "stack trace", "堆栈"],
            "log": ["log", "日志"],
        }
        return any(alias in text for kind in kinds for alias in aliases.get(kind, [kind]))
