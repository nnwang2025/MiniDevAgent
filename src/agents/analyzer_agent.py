from __future__ import annotations

import ast
from typing import Any

from ..llm_client import LLMClient
from ..tools.ast_tools import (
    extract_relevant_snippet,
    extract_symbol_snippet,
    extract_symbol_snippet_strict,
    get_function_skeleton,
    get_imports,
    parse_ast,
)
from ..tools.file_tools import read_file, summarize_file

ANALYZER_SYSTEM_PROMPT = """你是代码分析专家。给定代码文件内容和用户问题，你需要给出结构化的分析结果。

## 分析要求
1. 理解代码的职责和设计意图
2. 识别函数之间的调用关系
3. 找出与用户问题最相关的代码部分
4. 使用中文描述，保留代码标识符原文

## 输出格式 (JSON)
{
  "summary": "对文件功能的总体概述",
  "key_findings": ["发现1", "发现2"],
  "relevant_sections": ["相关的代码片段说明"],
  "architecture_role": "该文件/模块在项目中的角色",
  "call_chain": ["被调用的关键函数"]
}
"""


class AnalyzerAgent:
    """LLM-driven code analysis agent.

    Primary: LLM analyzes code files with structured output for deeper understanding.
    Fallback: AST-based extraction of skeletons, imports, calls, and snippets.
    """

    def __init__(self) -> None:
        self.llm = LLMClient()

    def analyze(
        self,
        project_path: str,
        file_paths: list[str],
        question: str,
        plan: dict[str, Any] | None = None,
        exploration: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Analyze the given files in the context of the question."""
        plan = plan or {}
        exploration = exploration or {}
        task_type = plan.get("task_type", "general_question")

        analysis: dict[str, Any] = {
            "task_type": task_type,
            "files": {},
            "file_tree": exploration.get("file_tree", ""),
            "dependency_trace": exploration.get("dependency_trace", {}),
            "search_tokens": exploration.get("search_tokens", []),
            "target_symbols": plan.get("target_symbols", []) or exploration.get("symbols", []),
            "target_area": plan.get("target_area", "") or exploration.get("target_area", ""),
            "planner_reason": plan.get("reason", ""),
        }

        if task_type == "project_overview":
            index = exploration.get("index", {})
            analysis["project_index"] = {
                "count": index.get("count", 0),
                "files": index.get("files", []),
            }
            if not file_paths:
                return analysis

        symbols = analysis["target_symbols"]

        for path in file_paths:
            # AST-based extraction (always available)
            summary = summarize_file(project_path, path)
            skeleton = get_function_skeleton(project_path, path)
            try:
                imports = get_imports(project_path, path)
            except Exception:
                imports = []

            calls = self._extract_calls(project_path, path)
            focused_snippets: dict[str, str] = {}
            for symbol in symbols[:6]:
                symbol_snippet = extract_symbol_snippet_strict(project_path, path, symbol)
                if symbol_snippet:
                    focused_snippets[symbol] = symbol_snippet

            # Determine the best snippet
            if task_type == "function_analysis" and symbols:
                snippet = extract_symbol_snippet(project_path, path, symbols[0])
            elif task_type in {"architecture_flow", "dependency_trace"} and focused_snippets:
                snippet = "\n\n".join(focused_snippets.values())
            else:
                snippet = extract_relevant_snippet(project_path, path, question)

            # LLM-enhanced analysis (if available)
            llm_analysis = self._llm_analyze_file(project_path, path, question, task_type)

            analysis["files"][path] = {
                "summary": llm_analysis.get("summary", summary) if llm_analysis else summary,
                "skeleton": skeleton,
                "imports": imports,
                "calls": calls,
                "architecture_role": (
                    llm_analysis.get("architecture_role", "")
                    if llm_analysis else self._infer_role(path, skeleton, imports)
                ),
                "snippet": snippet,
                "focused_snippets": focused_snippets,
                "llm_key_findings": llm_analysis.get("key_findings", []) if llm_analysis else [],
            }

        if task_type in {"architecture_flow", "dependency_trace"}:
            analysis["call_chain"] = self._build_call_chain(analysis["files"])

        return analysis

    # ── LLM analysis ─────────────────────────────────────────────

    def _llm_analyze_file(
        self, project_path: str, path: str, question: str, task_type: str
    ) -> dict[str, Any] | None:
        """Use LLM for deeper semantic analysis of a file."""
        if not self.llm.enabled:
            return None

        content = read_file(project_path, path)
        if len(content) > 8000:
            content = content[:8000] + "\n# ... (文件过长，已截断)"

        user_prompt = (
            f"## 文件路径\n{path}\n\n"
            f"## 用户问题\n{question}\n\n"
            f"## 任务类型\n{task_type}\n\n"
            f"## 文件内容\n```python\n{content}\n```"
        )
        result = self.llm.chat_json(ANALYZER_SYSTEM_PROMPT, user_prompt, timeout=30.0)
        return result

    # ── AST helpers ──────────────────────────────────────────────

    def _extract_calls(self, project_path: str, path: str) -> list[dict[str, Any]]:
        try:
            tree = parse_ast(project_path, path)
        except Exception:
            return []
        calls: list[dict[str, Any]] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = self._call_name(node.func)
            if name:
                calls.append({"name": name, "line": getattr(node, "lineno", 0)})
        return calls

    def _call_name(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            base = self._call_name(node.value)
            return f"{base}.{node.attr}" if base else node.attr
        return ""

    def _infer_role(self, path: str, skeleton: list[dict[str, Any]], imports: list[str]) -> str:
        names = {item.get("name", "") for item in skeleton}
        if path.endswith("main.py"):
            return "入口或启动模块"
        if "HarnessRuntime" in names:
            return "运行时编排模块"
        if any(name.endswith("Agent") for name in names):
            return "Agent 阶段模块"
        if path.endswith("_tools.py"):
            return "工具能力模块"
        if "\\api\\" in path or "/api/" in path:
            return "API 接口模块"
        if "\\memory\\" in path or "/memory/" in path:
            return "记忆/索引模块"
        return "支持模块"

    def _build_call_chain(self, files: dict[str, Any]) -> list[str]:
        chain: list[str] = []
        for path, metadata in files.items():
            for call in metadata.get("calls", []):
                name = call.get("name", "")
                if name and name not in chain:
                    chain.append(name)
        return chain[:20]
