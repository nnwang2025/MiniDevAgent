from __future__ import annotations

from typing import Any

from ..llm_client import LLMClient

CONTEXT_COMPRESSION_PROMPT = """你是上下文压缩专家。给定大量执行证据和任务类型，你需要判断哪些信息对回答问题有用，哪些可以丢弃。

## 压缩原则
- 保留: 与问题直接相关的文件路径、函数定义、代码片段、调用关系
- 丢弃: 与问题无关的全文 dump、未使用模块、低分记忆记录
- 对代码理解任务: 保留定义、签名、返回值、调用链
- 对代码编辑任务: 保留目标文件、diff、验证计划
- 对记忆任务: 保留高分记忆记录

## 输出格式 (JSON)
{
  "kept_keys": ["保留的字段名"],
  "dropped_keys": ["丢弃的字段名"],
  "summary": "压缩后内容的简要说明"
}
"""


class ContextAgent:
    """LLM-enhanced context compression agent.

    Can optionally use LLM to decide what to keep/drop from execution
    results before passing to the AnswerAgent.
    """

    def __init__(self) -> None:
        self.llm = LLMClient()

    def compress_context(
        self,
        analysis: dict[str, Any],
        recent_notes: list[str],
        task_type: str = "",
    ) -> dict[str, Any]:
        """Compress analysis results into a compact context for the answer agent.

        Args:
            analysis: Full analysis output from skills/exploration.
            recent_notes: Recent conversation history.
            task_type: The task type for context-aware compression.
        """
        # AST-based extraction (always runs)
        file_summaries = []
        skeletons = []
        snippets = []

        files = analysis.get("files", {})
        if isinstance(files, dict):
            for file_path, metadata in files.items():
                if isinstance(metadata, dict):
                    file_summaries.append(
                        f"{file_path}: {metadata.get('summary', '')}"
                    )
                    skeletons.append(
                        f"{file_path}: {len(metadata.get('skeleton', []))} definitions"
                    )
                    snippet = metadata.get("snippet")
                    if snippet:
                        snippets.append(f"{file_path}: {snippet}")

        compressed = {
            "task_type": analysis.get("task_type", task_type or "general_question"),
            "summary": "\n".join(file_summaries[:5]),
            "skeletons": "\n".join(skeletons[:5]),
            "snippets": "\n".join(snippets[:5]),
            "file_tree": analysis.get("file_tree", ""),
            "dependency_trace": analysis.get("dependency_trace", {}),
            "project_index": analysis.get("project_index", {}),
            "conversation_history": "\n".join(recent_notes[-5:]),
        }

        # LLM-enhanced compression
        if self.llm.enabled:
            llm_compressed = self._llm_compress(compressed, task_type)
            if llm_compressed:
                return llm_compressed

        return compressed

    def _llm_compress(
        self, compressed: dict[str, Any], task_type: str
    ) -> dict[str, Any] | None:
        """Use LLM to decide what to keep/drop."""
        # Build a description of what we have
        available_keys = {k: str(v)[:200] for k, v in compressed.items() if v}
        user_prompt = (
            f"## 任务类型\n{task_type}\n\n"
            f"## 可用上下文\n"
            + "\n".join(f"- {k}: {v}" for k, v in available_keys.items())
            + "\n\n请判断哪些信息应该保留，哪些可以丢弃。"
        )
        result = self.llm.chat_json(
            CONTEXT_COMPRESSION_PROMPT, user_prompt, timeout=20.0
        )
        if not result:
            return None

        # Build filtered context
        kept_keys = set(result.get("kept_keys", []))
        filtered = {
            k: v for k, v in compressed.items()
            if k in kept_keys or k in ("task_type", "question")
        }
        filtered["_compression"] = "llm"
        filtered["_compression_summary"] = result.get("summary", "")
        return filtered
