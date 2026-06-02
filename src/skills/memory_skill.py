from __future__ import annotations

from typing import Any

from .base import BaseSkill


class MemoryRecallSkill(BaseSkill):
    name = "MemoryRecallSkill"

    def run(self, question: str, context: dict[str, Any], runtime_state: Any) -> dict[str, Any]:
        self.bind(runtime_state)
        cold = runtime_state.cold_memory
        records = cold.retrieve(question, limit=5)
        if not records:
            answer = "当前持久化 cold memory 中没有找到与该问题相关的历史记录。"
            return self.ok(
                answer,
                cold.sources,
                [],
                "已检索 memory_records/episodic_summaries/entity_memory/relationship_memory，未命中可引用记录。",
                related_records=[],
                tools_used=["ColdMemory.retrieve"],
                observations={"memory_sources": cold.sources, "chunks": [], "raw_evidence": []},
                trace_observation={"cold_memory_sources": cold.sources, "hot_memory_used": False, "matches": 0},
            )

        lines = []
        evidence = []
        related_records = []
        chunks = []
        raw_evidence = []
        for record in records:
            when = f"（{record['timestamp']}）" if record.get("timestamp") else ""
            breakdown = record.get("score_breakdown", {})
            lines.append(f"- {record['text']}{when} score={record.get('score')}, breakdown={breakdown}")
            evidence.append(f"{record['kind']} | {'/'.join(record.get('retrieval_methods', []))} | {record.get('source', 'ingested')} | {breakdown}")
            related_records.append(record["text"])
            if record.get("chunk"):
                chunks.append(record["chunk"])
            else:
                raw_evidence.append(record)
        answer = "找到以下持久化 cold memory 记录：\n" + "\n".join(lines)
        return self.ok(
            answer,
            cold.sources,
            evidence,
            "来自持久化 cold memory 的检索结果，按 exact/entity/BM25/vector/recency 综合 rerank。",
            related_records=related_records,
            tools_used=["ColdMemory.retrieve", "build_episodic_chunks"],
            observations={"memory_sources": cold.sources, "chunks": chunks, "raw_evidence": raw_evidence},
            trace_observation={"cold_memory_sources": cold.sources, "hot_memory_used": False, "matches": len(records)},
        )
