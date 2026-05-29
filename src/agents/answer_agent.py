from __future__ import annotations

import os
from typing import Any

from ..llm_client import LLMClient


class AnswerAgent:
    def __init__(self) -> None:
        self.llm = LLMClient()

    def answer(self, question: str, context: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any]:
        files_used = self._as_list(analysis.get("files_used", []))
        evidence = self._as_list(analysis.get("evidence", []))
        core_answer = str(analysis.get("answer", "没有生成可用回答。")).strip()
        summary = str(analysis.get("analysis_summary", "")).strip()
        compact_context = context.get("compressed_context", {})
        answer_text = self._polish_with_llm(question, core_answer, files_used, evidence, summary, compact_context)
        if not answer_text:
            answer_text = self._format(core_answer, files_used, evidence, summary)
        return {
            "answer": answer_text,
            "files_used": files_used,
            "evidence": evidence,
            "analysis_summary": summary,
            "related_records": self._as_list(analysis.get("related_records", [])),
        }

    def _format(self, answer: str, files_used: list[str], evidence: list[str], summary: str) -> str:
        basis = evidence[:8] or ([summary] if summary else [])
        return "\n\n".join(
            [
                "【回答】\n" + answer,
                "【关键文件】\n" + self._bullet(files_used[:8], "无"),
                "【代码/记忆证据】\n" + self._bullet([self._compact(item) for item in basis], "无可引用证据"),
            ]
        )

    def _polish_with_llm(
        self,
        question: str,
        answer: str,
        files_used: list[str],
        evidence: list[str],
        summary: str,
        compact_context: dict[str, Any],
    ) -> str:
        if not self.llm.enabled or os.getenv("ENABLE_LLM_ANSWER", "").lower() not in {"1", "true", "yes"}:
            return ""
        system = (
            "你是代码工程助手。必须严格基于用户给出的证据，用中文回答。"
            "只输出【回答】【关键文件】【代码/记忆证据】三段，不要补充未提供的文件、调用关系或历史记录。"
        )
        user = (
            f"问题：{question}\n结论：{answer}\n文件：{files_used}\n"
            f"证据：{evidence[:8]}\n上下文：{compact_context}\n摘要：{summary}"
        )
        content = self.llm.chat(system, user, timeout=20.0)
        if all(marker in content for marker in ["【回答】", "【关键文件】", "【代码/记忆证据】"]):
            return content
        return ""

    def _compact(self, text: str) -> str:
        single_line = " ".join(text.split())
        return single_line if len(single_line) <= 220 else single_line[:217] + "..."

    def _bullet(self, values: list[str], empty: str) -> str:
        return "\n".join(f"- {value}" for value in values) if values else f"- {empty}"

    def _as_list(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if str(item).strip()]
