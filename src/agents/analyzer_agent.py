from __future__ import annotations
import ast
from typing import Any
from ..tools.ast_tools import extract_relevant_snippet, extract_symbol_snippet, extract_symbol_snippet_strict, get_function_skeleton, get_imports, parse_ast
from ..tools.file_tools import read_file, summarize_file


class AnalyzerAgent:
    def analyze(
        self,
        project_path: str,
        file_paths: list[str],
        question: str,
        plan: dict[str, Any] | None = None,
        exploration: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
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
            if task_type == "function_analysis" and symbols:
                snippet = extract_symbol_snippet(project_path, path, symbols[0])
            elif task_type in {"architecture_flow", "dependency_trace"} and focused_snippets:
                snippet = "\n\n".join(focused_snippets.values())
            else:
                snippet = extract_relevant_snippet(project_path, path, question)
            analysis["files"][path] = {
                "summary": summary,
                "skeleton": skeleton,
                "imports": imports,
                "calls": calls,
                "architecture_role": self._infer_role(path, skeleton, imports),
                "snippet": snippet,
                "focused_snippets": focused_snippets,
            }
        if task_type in {"architecture_flow", "dependency_trace"}:
            analysis["call_chain"] = self._build_call_chain(analysis["files"])
        return analysis

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
