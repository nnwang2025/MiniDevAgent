from __future__ import annotations

from typing import Any

from .base import BaseSkill


class ProblemTraceSkill(BaseSkill):
    name = "ProblemTraceSkill"

    def run(self, question: str, context: dict[str, Any], runtime_state: Any) -> dict[str, Any]:
        self.bind(runtime_state)
        cold = runtime_state.cold_memory
        records = cold.retrieve(question, kinds={"issue", "error", "traceback", "log"}, limit=5)
        if not records:
            answer = "未找到真实日志、traceback、issue 或 error memory 证据，因此不能判断那个 bug 的原因或解决方案。"
            return self.ok(
                answer,
                cold.sources,
                [],
                "只检索 issue/error/traceback/log 冷记忆块；无证据时明确未找到。",
                related_records=[],
                tools_used=["ColdMemory.retrieve"],
                observations={"problem_sources": cold.sources, "records": []},
                trace_observation={"problem_sources": cold.sources, "matches": 0},
            )

        statements = []
        evidence = []
        related_records = []
        for record in records:
            statements.append(f"- {record['text']} score={record.get('score')} breakdown={record.get('score_breakdown', {})}")
            evidence.append(f"{record['kind']} | {'/'.join(record.get('retrieval_methods', []))} | {record.get('source', 'ingested')} | {record.get('score_breakdown', {})}")
            related_records.append(record["text"])
        answer = "找到与该问题相关的真实问题证据：\n" + "\n".join(statements)
        return self.ok(
            answer,
            cold.sources,
            evidence,
            "只引用持久化 issue/error/traceback/log 记忆块的检索结果。",
            related_records=related_records,
            tools_used=["ColdMemory.retrieve"],
            observations={"problem_sources": cold.sources, "records": records},
            trace_observation={"problem_sources": cold.sources, "matches": len(records)},
        )
