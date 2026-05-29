from __future__ import annotations

import ast
from typing import Any

from ..tools.ast_tools import find_symbol_definitions, get_function_skeleton, get_imports, parse_ast
from ..tools.file_tools import list_files, read_file
from ..tools.search_tools import search_references, trace_file_dependencies
from .base import MCPServer


class CodeAnalysisMCPServer(MCPServer):
    name = "code_analysis"

    def parse_ast(self, path: str) -> dict[str, Any]:
        module = parse_ast(str(self.project_root), path)
        return {"path": path, "node_count": sum(1 for _ in ast.walk(module)), "symbols": get_function_skeleton(str(self.project_root), path)}

    def find_symbol(self, symbol: str) -> dict[str, Any]:
        matches = find_symbol_definitions(str(self.project_root), list_files(str(self.project_root)), symbol)
        return {"symbol": symbol, "matches": matches}

    def trace_dependency(self, paths: list[str]) -> dict[str, Any]:
        return {"trace": trace_file_dependencies(str(self.project_root), paths)}

    def find_entrypoint(self) -> dict[str, Any]:
        entries = []
        for path in list_files(str(self.project_root)):
            parts = {part.lower() for part in path.replace("\\", "/").split("/")}
            if parts.intersection({"prompts", "docs", "tests", "skills", "scripts"}):
                continue
            try:
                module = parse_ast(str(self.project_root), path)
            except Exception:
                continue
            source = read_file(str(self.project_root), path)
            for node in ast.walk(module):
                if isinstance(node, ast.If) and ast.unparse(node.test).replace("'", '"') == '__name__ == "__main__"':
                    entries.append({"path": path, "line": node.lineno, "kind": "__main__"})
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in {"main", "create_app", "bootstrap"}:
                    entries.append({"path": path, "line": node.lineno, "kind": node.name})
                elif isinstance(node, ast.Assign) and "FastAPI(" in ast.unparse(node):
                    entries.append({"path": path, "line": node.lineno, "kind": "fastapi_app"})
            if "uvicorn.run(" in source:
                entries.append({"path": path, "line": 1, "kind": "uvicorn"})
        return {"entries": entries}

    def search_references(self, symbol: str) -> dict[str, Any]:
        return {"symbol": symbol, "references": search_references(str(self.project_root), symbol)}
