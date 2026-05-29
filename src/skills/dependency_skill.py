from __future__ import annotations

import ast
import re
from typing import Any

from .base import BaseSkill


class DependencyTraceSkill(BaseSkill):
    name = "DependencyTraceSkill"

    def run(self, question: str, context: dict[str, Any], runtime_state: Any) -> dict[str, Any]:
        self.bind(runtime_state)
        targets = self._target_symbols(question, context)
        if not targets:
            return self.ok("没有识别到需要追踪的类、函数或初始化目标。", [], [], "依赖追踪缺少目标符号。", tools_used=["ast.parse"], trace_observation={"target": ""})

        all_defs: list[dict[str, Any]] = []
        all_imports: list[dict[str, Any]] = []
        all_calls: list[dict[str, Any]] = []
        snippets: list[str] = []
        for symbol in targets:
            definitions, imports, calls = self._ast_events(symbol)
            all_defs.extend(definitions)
            all_imports.extend(imports)
            all_calls.extend(calls)
        events = all_defs + all_imports + all_calls
        files = self._dedupe([event["path"] for event in events])
        if not files:
            return self.ok(f"没有在真实代码中找到 {', '.join(targets)} 的定义或调用。", [], [], "AST 依赖追踪无命中。", tools_used=["ast.parse"], trace_observation={"targets": targets, "matches": 0})

        for event in (all_defs + all_calls)[:8]:
            snippets.append(f"{event['path']}:{event['line']}\n{self._line_window(event['path'], event['line'])}")
        answer = "\n".join(
            [
                self._conclusion(targets, all_defs, all_calls),
                "",
                "调用/依赖证据：",
                *[f"- {event['path']}:{event['line']} {event['message']}" for event in events],
            ]
        )
        evidence = [f"{event['path']}:{event['line']} {event['message']}" for event in events]
        return self.ok(
            answer,
            files,
            evidence,
            "只使用源代码 AST 中的定义、import 和调用点，已过滤 prompts/docs/tests/skills 自身实现文件。",
            tools_used=["list_files", "ast.parse", "ast.walk", "read_file"],
            observations={"target": targets, "definitions": all_defs, "imports": all_imports, "calls": all_calls, "snippets": snippets},
            trace_observation={"targets": targets, "files_used": files, "definition_count": len(all_defs), "call_count": len(all_calls)},
        )

    def _target_symbols(self, question: str, context: dict[str, Any]) -> list[str]:
        symbols = context.get("target_symbols") or []
        if symbols:
            return symbols
        if "数据库" in question or "database" in question.lower():
            return ["get_default_connection", "DatabaseConnection"]
        explicit = re.findall(r"\b([A-Z][A-Za-z0-9_]+|[a-z_][A-Za-z0-9_]+)\b", question)
        return [item for item in explicit if item not in {"where", "who", "call"}][:1]

    def _ast_events(self, symbol: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        definitions: list[dict[str, Any]] = []
        imports: list[dict[str, Any]] = []
        calls: list[dict[str, Any]] = []
        for path in self.files():
            if not self._allowed_path(path):
                continue
            try:
                tree = self.ast(path)
            except Exception:
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == symbol:
                    definitions.append({"path": path, "line": node.lineno, "message": f"定义 {symbol}"})
                elif isinstance(node, ast.ImportFrom) and any(alias.name == symbol for alias in node.names):
                    module = "." * node.level + (node.module or "")
                    imports.append({"path": path, "line": node.lineno, "message": f"从 {module} 导入 {symbol}"})
                elif isinstance(node, ast.Import) and any(alias.name.split(".")[-1] == symbol for alias in node.names):
                    imports.append({"path": path, "line": node.lineno, "message": f"导入 {symbol}"})
                elif isinstance(node, ast.Call):
                    called = self._call_name(node.func)
                    if called == symbol or called.endswith("." + symbol):
                        calls.append({"path": path, "line": node.lineno, "message": f"调用 {called}(...)"})
        return definitions, imports, calls

    def _allowed_path(self, path: str) -> bool:
        parts = {part.lower() for part in path.replace("\\", "/").split("/")}
        return not parts.intersection({"prompts", "docs", "tests", "skills"})

    def _conclusion(self, targets: list[str], definitions: list[dict[str, Any]], calls: list[dict[str, Any]]) -> str:
        if calls:
            locations = ", ".join(f"{item['path']}:{item['line']}" for item in calls[:8])
            return f"{', '.join(targets)} 的实际调用点位于 {locations}。"
        if definitions:
            return f"找到了 {', '.join(targets)} 的定义，但没有发现直接调用点。"
        return f"发现了 {', '.join(targets)} 的引用，但没有定位到本项目内定义。"

    def _line_window(self, path: str, line: int) -> str:
        lines = self.read(path).splitlines()
        start = max(0, line - 2)
        end = min(len(lines), line + 1)
        return "\n".join(lines[start:end]).strip()
