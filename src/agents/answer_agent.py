from __future__ import annotations

from typing import Any

from ..llm_client import LLMClient
from ..prompts.answer_prompt import ANSWER_SYSTEM_PROMPT


class AnswerAgent:
    """LLM-first answer generator with rule-based fallback.

    Takes the execution result (from skill or PERR loop), compressed context,
    and original question, then produces a polished, evidence-backed answer.
    """

    def __init__(self) -> None:
        self.llm = LLMClient()

    def answer(
        self,
        question: str,
        context: dict[str, Any],
        analysis: dict[str, Any],
    ) -> dict[str, Any]:
        """Generate the final answer.

        Args:
            question: Original user question.
            context: {"plan": {...}, "compressed_context": {...}} from the pipeline.
            analysis: Skill execution result (answer, files_used, evidence, etc.).
        """
        files_used = self._as_list(analysis.get("files_used", []))
        evidence = self._as_list(analysis.get("evidence", []))
        core_answer = str(analysis.get("answer", "没有生成可用回答。")).strip()
        summary = str(analysis.get("analysis_summary", "")).strip()
        compact_context = context.get("compressed_context", {})
        plan = context.get("plan", {})

        # LLM primary
        answer_text = self._polish_with_llm(
            question, core_answer, files_used, evidence, summary,
            compact_context, plan,
        )

        # Rule fallback
        if not answer_text:
            answer_text = self._format(core_answer, files_used, evidence, summary)

        return {
            "answer": answer_text,
            "files_used": files_used,
            "evidence": evidence,
            "analysis_summary": summary,
            "related_records": self._as_list(analysis.get("related_records", [])),
        }

    # ── Rule-based formatting (fallback) ──────────────────────────

    def _format(
        self, answer: str, files_used: list[str], evidence: list[str], summary: str
    ) -> str:
        basis = evidence[:8] or ([summary] if summary else [])
        return "\n\n".join(
            [
                "【回答】\n" + answer,
                "【关键文件】\n" + self._bullet(files_used[:8], "无"),
                "【代码/记忆证据】\n" + self._bullet(
                    [self._compact(item) for item in basis], "无可引用证据"
                ),
            ]
        )

    # ── LLM polishing (primary) ──────────────────────────────────

    def _polish_with_llm(
        self,
        question: str,
        core_answer: str,
        files_used: list[str],
        evidence: list[str],
        summary: str,
        compact_context: dict[str, Any],
        plan: dict[str, Any],
    ) -> str:
        """Use LLM to synthesize a polished, evidence-backed answer."""
        if not self.llm.enabled:
            return ""

        task_type = plan.get("task_type", "unknown")
        user_prompt = self._build_polish_prompt(
            question, core_answer, files_used, evidence, summary,
            compact_context, task_type,
        )

        content = self.llm.chat(ANSWER_SYSTEM_PROMPT, user_prompt, timeout=30.0)

        # Validate: must contain the three expected sections
        if content and all(
            marker in content
            for marker in ["【回答】", "【关键文件】", "【代码/记忆证据】"]
        ):
            return content

        # Partial answer — accept if it has at least the answer section
        if content and "【回答】" in content:
            return content

        return ""

    def _build_polish_prompt(
        self,
        question: str,
        core_answer: str,
        files_used: list[str],
        evidence: list[str],
        summary: str,
        compact_context: dict[str, Any],
        task_type: str,
    ) -> str:
        """Build a structured prompt for the LLM to polish the answer."""
        parts = [
            f"## 用户问题\n{question}",
            f"## 任务类型\n{task_type}",
            "",
            f"## 核心结论\n{core_answer}",
            "",
            f"## 关键文件\n{chr(10).join(f'- {f}' for f in files_used[:10]) if files_used else '(无)'}",
            "",
            f"## 证据\n{chr(10).join(f'- {self._compact(e)}' for e in evidence[:8]) if evidence else '(无)'}",
        ]

        if summary:
            parts.append(f"\n## 分析摘要\n{summary}")

        if compact_context:
            ctx_parts = []
            for key, value in compact_context.items():
                if key in ("question", "task_type"):
                    continue
                if isinstance(value, list):
                    ctx_parts.append(f"{key}: {value[:5]}")
                elif isinstance(value, str) and value:
                    ctx_parts.append(f"{key}: {value[:300]}")
            if ctx_parts:
                parts.append(f"\n## 压缩上下文\n{chr(10).join(ctx_parts)}")

        parts.append("\n请综合以上信息，用【回答】【关键文件】【代码/记忆证据】三段格式输出。")
        return "\n".join(parts)

    # ── helpers ───────────────────────────────────────────────────

    def _compact(self, text: str) -> str:
        single_line = " ".join(text.split())
        return single_line if len(single_line) <= 220 else single_line[:217] + "..."

    def _bullet(self, values: list[str], empty: str) -> str:
        return (
            "\n".join(f"- {value}" for value in values)
            if values
            else f"- {empty}"
        )

    def _as_list(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if str(item).strip()]
