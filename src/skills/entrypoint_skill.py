from __future__ import annotations

import ast
from typing import Any

from .base import BaseSkill


class EntrypointSkill(BaseSkill):
    name = "EntrypointSkill"

    def run(self, question: str, context: dict[str, Any], runtime_state: Any) -> dict[str, Any]:
        self.bind(runtime_state)
        entries = self._detect_entries()
        if not entries:
            return self.ok("没有发现 __main__、FastAPI app、CLI main()、create_app() 或 uvicorn 启动入口。", [], [], "入口 AST 扫描无结果。", tools_used=["ast.parse"], trace_observation={"entries": []})

        primary = entries[:10]
        files = self._dedupe([entry["path"] for entry in primary])
        answer = "\n".join(
            [
                f"最主要入口：{primary[0]['path']}:{primary[0]['line']}，{primary[0]['description']}",
                "",
                "启动入口证据：",
                *[f"- {entry['path']}:{entry['line']} {entry['description']}" for entry in primary],
            ]
        )
        evidence = [f"{entry['path']}:{entry['line']} {entry['evidence']}" for entry in primary]
        return self.ok(
            answer,
            files,
            evidence,
            "识别 __main__、FastAPI app、CLI main()、create_app() 和 uvicorn 启动入口。",
            tools_used=["list_files", "ast.parse", "ast.walk"],
            observations={"entry_files": files, "entries": primary},
            trace_observation={"entries": [f"{entry['path']}:{entry['line']} {entry['kind']}" for entry in primary]},
        )

    def _detect_entries(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for path in self.files():
            if not self._allowed_path(path):
                continue
            try:
                tree = self.ast(path)
            except Exception:
                continue
            source = self.read(path)
            for node in ast.walk(tree):
                if isinstance(node, ast.If) and self._is_main_guard(node.test):
                    entries.append(self._entry(path, node.lineno, "__main__", f"脚本通过 __main__ 保护启动{self._guard_calls(node)}。", '__name__ == "__main__"'))
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "main":
                    entries.append(self._entry(path, node.lineno, "cli_main", "定义 CLI main() 函数。", "def main(...)"))
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "create_app":
                    entries.append(self._entry(path, node.lineno, "create_app", "定义应用工厂 create_app()。", "def create_app(...)"))
                elif isinstance(node, ast.Assign) and "FastAPI(" in ast.unparse(node):
                    entries.append(self._entry(path, node.lineno, "fastapi_app", "创建 FastAPI 应用对象。", ast.unparse(node)))
                elif isinstance(node, ast.Call) and self._call_name(node.func).endswith("uvicorn.run"):
                    entries.append(self._entry(path, node.lineno, "uvicorn", "通过 uvicorn.run 启动 ASGI 应用。", ast.unparse(node)))
            if "uvicorn.run(" in source and not any(entry["path"] == path and entry["kind"] == "uvicorn" for entry in entries):
                entries.append(self._entry(path, 1, "uvicorn", "文件中包含 uvicorn.run 启动调用。", "uvicorn.run(...)"))
        return sorted(entries, key=lambda item: (self._priority(item["path"], item["kind"]), item["path"], item["line"]))

    def _entry(self, path: str, line: int, kind: str, description: str, evidence: str) -> dict[str, Any]:
        return {"path": path, "line": line, "kind": kind, "description": description, "evidence": evidence}

    def _allowed_path(self, path: str) -> bool:
        parts = {part.lower() for part in path.replace("\\", "/").split("/")}
        return not parts.intersection({"skills", "prompts", "tests", "docs", "scripts"})

    def _priority(self, path: str, kind: str) -> int:
        normalized = path.replace("\\", "/").lower()
        base = {"src/main.py": 0, "main.py": 10, "src/api/app.py": 20, "api/app.py": 30}.get(normalized, 50)
        kind_offset = {"__main__": 0, "cli_main": 1, "uvicorn": 2, "fastapi_app": 3, "create_app": 4}.get(kind, 9)
        return base + kind_offset

    def _is_main_guard(self, test: ast.AST) -> bool:
        return isinstance(test, ast.Compare) and ast.unparse(test).replace("'", '"') == '__name__ == "__main__"'

    def _guard_calls(self, node: ast.If) -> str:
        calls = [self._call_name(child.func) for child in ast.walk(node) if isinstance(child, ast.Call)]
        calls = [call for call in calls if call and call != "print"]
        return f"，并调用 {calls[0]}()" if calls else ""
