from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..llm_client import LLMClient
from ..memory.code_index import CodeIndex
from ..tools.ast_tools import find_symbol_definitions
from ..tools.file_tools import build_project_index, list_files, search_by_filename
from ..tools.search_tools import search_code, search_references, trace_file_dependencies

EXPLORER_SYSTEM_PROMPT = """你是代码探索专家。给定用户问题和项目文件列表，你需要选出最相关的文件来回答问题。

## 选择原则
1. 优先选择包含问题中提到符号（函数名、类名）的文件
2. 其次选择文件名或路径与问题关键词匹配的文件
3. 对于入口问题，优先 main.py, app.py, routes.py
4. 对于依赖问题，需要覆盖定义文件和调用文件
5. 最多选 8 个文件，按相关度排序

## 输出格式 (JSON)
{
  "selected_files": ["路径1", "路径2"],
  "reasoning": "为什么选这些文件",
  "search_strategy": "symbol_search|keyword_search|overview_selection|dependency_graph"
}
"""

STOPWORDS = {
    "about", "where", "what", "when", "which", "would", "could", "should", "does",
    "this", "that", "with", "from", "into", "through", "implemented", "implementation", "explain",
    "analyze", "function", "method", "class", "file", "files", "project", "codebase",
    "the", "app",
}


class ExplorerAgent:
    """LLM-driven file selection agent.

    Primary: LLM selects the most relevant files based on the question + project index.
    Fallback: Rule-based scoring using keyword matches, symbol locations, and dependency graph.
    """

    def __init__(self) -> None:
        self.llm = LLMClient()

    def explore(
        self, project_path: str, question: str, plan: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Select candidate files for answering the question."""
        plan = plan or {}
        task_type = plan.get("task_type", "general_question")
        index_data = build_project_index(project_path)
        code_index = CodeIndex(root=index_data["root"], files=index_data["files"])
        keywords = self._dedupe((plan.get("search_tokens") or []) + self._keywords(question))
        symbols = self._dedupe((plan.get("target_symbols") or []) + self._symbols(question))
        project_files = list_files(project_path)
        file_mentions = self._dedupe(
            (plan.get("candidate_files_hint") or []) + self._file_mentions(project_path, question)
        )
        file_mentions = self._existing_files(file_mentions, project_files)

        # Try LLM selection
        if self.llm.enabled:
            llm_candidates = self._llm_select(question, task_type, project_files, symbols, keywords)
            if llm_candidates:
                return self._build_result(
                    task_type, index_data, llm_candidates, keywords, symbols, plan, project_path, "llm"
                )

        # Rule-based fallback
        candidates = self._rule_select(task_type, project_path, project_files, code_index, file_mentions, symbols, keywords, plan)
        return self._build_result(
            task_type, index_data, candidates, keywords, symbols, plan, project_path, "rule"
        )

    # ── LLM selection ────────────────────────────────────────────

    def _llm_select(
        self,
        question: str,
        task_type: str,
        project_files: list[str],
        symbols: list[str],
        keywords: list[str],
    ) -> list[str] | None:
        """Use LLM to select the most relevant files."""
        files_preview = "\n".join(project_files[:60])
        user_prompt = (
            f"## 用户问题\n{question}\n\n"
            f"## 任务类型\n{task_type}\n\n"
            f"## 已知符号\n{symbols if symbols else '(无)'}\n\n"
            f"## 已知关键词\n{keywords[:8] if keywords else '(无)'}\n\n"
            f"## 项目文件列表（共 {len(project_files)} 个，显示前 60 个）\n{files_preview}"
        )
        result = self.llm.chat_json(EXPLORER_SYSTEM_PROMPT, user_prompt, timeout=30.0)
        if not result:
            return None
        selected = result.get("selected_files", [])
        if isinstance(selected, list) and len(selected) > 0:
            return self._existing_files(selected, project_files)
        return None

    # ── Rule-based selection (fallback) ──────────────────────────

    def _rule_select(
        self,
        task_type: str,
        project_path: str,
        project_files: list[str],
        code_index: CodeIndex,
        file_mentions: list[str],
        symbols: list[str],
        keywords: list[str],
        plan: dict[str, Any],
    ) -> list[str]:
        """Fallback rule-based file selection."""
        if task_type == "project_overview":
            return file_mentions or self._overview_candidates(project_files, plan.get("target_area", ""))
        if task_type == "function_analysis":
            return self._function_candidates(project_path, file_mentions, symbols, keywords)
        if task_type in {"architecture_flow", "dependency_trace"}:
            return self._architecture_candidates(project_path, code_index, file_mentions, symbols, keywords)
        return self._search_candidates(project_path, code_index, keywords)

    def _build_result(
        self,
        task_type: str,
        index_data: dict[str, Any],
        candidates: list[str],
        keywords: list[str],
        symbols: list[str],
        plan: dict[str, Any],
        project_path: str,
        strategy: str,
    ) -> dict[str, Any]:
        dep_trace = {}
        if task_type in {"architecture_flow", "dependency_trace"} and candidates:
            dep_trace = trace_file_dependencies(project_path, candidates)
        return {
            "index": index_data,
            "candidates": candidates,
            "search_tokens": keywords[:6],
            "symbols": symbols,
            "file_tree": index_data.get("tree", ""),
            "task_type": task_type,
            "target_area": plan.get("target_area", ""),
            "dependency_trace": dep_trace,
            "selection_strategy": strategy,
        }

    # ── keyword / symbol extraction ──────────────────────────────

    def _keywords(self, question: str) -> list[str]:
        words = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", question)
        return [word for word in words if len(word) > 2 and word.lower() not in STOPWORDS]

    def _symbols(self, question: str) -> list[str]:
        explicit = re.findall(r"`?([A-Za-z_][A-Za-z0-9_]*)\s*\(\)`?", question)
        quoted = re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", question)
        return list(dict.fromkeys(explicit + quoted))

    def _file_mentions(self, project_path: str, question: str) -> list[str]:
        mentioned = set(re.findall(r"[\w/\\.-]+\.py", question))
        files = list_files(project_path)
        matches: list[str] = []
        for file_path in files:
            if file_path in mentioned or Path(file_path).name in mentioned:
                matches.append(file_path)
        return matches

    # ── candidate selection helpers ──────────────────────────────

    def _function_candidates(
        self, project_path: str, file_mentions: list[str], symbols: list[str], keywords: list[str]
    ) -> list[str]:
        search_scope = file_mentions or list_files(project_path)
        for symbol in symbols + keywords[:2]:
            definitions = find_symbol_definitions(project_path, search_scope, symbol)
            if definitions:
                return [match["path"] for match in definitions]
        if file_mentions:
            return file_mentions
        ci = CodeIndex(root=project_path, files=build_project_index(project_path)["files"])
        return self._search_candidates(project_path, ci, keywords)[:2]

    def _architecture_candidates(
        self, project_path: str, code_index: CodeIndex,
        file_mentions: list[str], symbols: list[str], keywords: list[str],
    ) -> list[str]:
        candidates = list(file_mentions)
        files = list_files(project_path)
        if "main.py" in files:
            candidates.insert(0, "main.py")
        for symbol in symbols + keywords[:2]:
            candidates.extend(
                match["path"] for match in find_symbol_definitions(project_path, files, symbol)
            )
            candidates.extend(
                result["path"] for result in search_references(project_path, symbol)
            )
        if not candidates:
            candidates.extend(self._search_candidates(project_path, code_index, keywords)[:3])
        dependencies = trace_file_dependencies(project_path, list(dict.fromkeys(candidates)))
        for paths in dependencies.values():
            candidates.extend(paths)
        return candidates[:6]

    def _search_candidates(
        self, project_path: str, code_index: CodeIndex, keywords: list[str]
    ) -> list[str]:
        scores: dict[str, int] = {}
        for token in keywords[:4]:
            for entry in code_index.find_by_keyword(token):
                scores[entry["path"]] = scores.get(entry["path"], 0) + 2
            for path in search_by_filename(project_path, token):
                scores[path] = scores.get(path, 0) + 3
            for result in search_code(project_path, token):
                scores[result["path"]] = scores.get(result["path"], 0) + 5
        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        return [path for path, _ in ranked[:4]]

    def _overview_candidates(self, project_files: list[str], target_area: str) -> list[str]:
        if target_area and target_area != "project":
            prefix = f"src\\{target_area}\\"
            return [path for path in project_files if path.startswith(prefix)][:8]
        preferred = [
            "src\\main.py",
            "src\\runtime\\harness.py",
            "src\\agents\\planner_agent.py",
            "src\\agents\\explorer_agent.py",
            "src\\agents\\analyzer_agent.py",
            "src\\agents\\context_agent.py",
            "src\\agents\\answer_agent.py",
            "src\\tools\\file_tools.py",
            "src\\tools\\ast_tools.py",
            "src\\api\\routes.py",
        ]
        return [path for path in preferred if path in project_files]

    def _existing_files(self, hints: list[str], project_files: list[str]) -> list[str]:
        by_normalized = {path.replace("/", "\\").lower(): path for path in project_files}
        result: list[str] = []
        for hint in hints:
            normalized = hint.replace("/", "\\").lower()
            if normalized in by_normalized:
                result.append(by_normalized[normalized])
                continue
            name_matches = [
                path for path in project_files
                if path.lower().endswith("\\" + normalized) or path.lower() == normalized
            ]
            result.extend(name_matches)
        return self._dedupe(result)

    def _dedupe(self, values: list[str]) -> list[str]:
        result: list[str] = []
        for value in values:
            item = value.strip()
            if item and item not in result:
                result.append(item)
        return result
