from __future__ import annotations

import ast
from typing import Any

from ..tools.ast_tools import get_function_skeleton, get_imports, parse_ast
from ..tools.file_tools import build_project_index, list_files, read_file, summarize_file
from ..tools.search_tools import search_references, trace_file_dependencies


class BaseSkill:
    name = "base"

    def run(self, question: str, context: dict[str, Any], runtime_state: Any) -> dict[str, Any]:
        raise NotImplementedError

    @property
    def project_path(self) -> str:
        return self._project_path

    def bind(self, runtime_state: Any) -> None:
        self._project_path = runtime_state.project_path

    def ok(self, answer: str, files: list[str], evidence: list[str], summary: str, **extra: Any) -> dict[str, Any]:
        return {
            "answer": answer,
            "files_used": self._dedupe(files),
            "evidence": evidence,
            "analysis_summary": summary,
            **extra,
        }

    def files(self) -> list[str]:
        return list_files(self.project_path)

    def index(self) -> dict[str, Any]:
        return build_project_index(self.project_path)

    def read(self, path: str) -> str:
        return read_file(self.project_path, path)

    def summary(self, path: str) -> str:
        return summarize_file(self.project_path, path)

    def skeleton(self, path: str) -> list[dict[str, Any]]:
        return get_function_skeleton(self.project_path, path)

    def imports(self, path: str) -> list[str]:
        try:
            return get_imports(self.project_path, path)
        except Exception:
            return []

    def ast(self, path: str) -> ast.Module:
        return parse_ast(self.project_path, path)

    def references(self, symbol: str) -> list[dict[str, Any]]:
        return search_references(self.project_path, symbol)

    def dependency_trace(self, paths: list[str]) -> dict[str, list[str]]:
        return trace_file_dependencies(self.project_path, paths)

    def _dedupe(self, values: list[str]) -> list[str]:
        result: list[str] = []
        for value in values:
            if value and value not in result:
                result.append(value)
        return result

    def _call_name(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            base = self._call_name(node.value)
            return f"{base}.{node.attr}" if base else node.attr
        return ""

    def _extract_calls(self, path: str) -> list[dict[str, Any]]:
        try:
            tree = self.ast(path)
        except Exception:
            return []
        calls: list[dict[str, Any]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = self._call_name(node.func)
                if name:
                    calls.append({"name": name, "line": getattr(node, "lineno", 0)})
        return calls

    def _snippet_by_lines(self, path: str, start: int, end: int) -> str:
        lines = self.read(path).splitlines()
        return "\n".join(lines[max(start - 1, 0):end]).strip()
