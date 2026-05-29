from __future__ import annotations

import ast
import re
from typing import Any

from .base import BaseSkill


class FunctionExplainSkill(BaseSkill):
    name = "FunctionExplainSkill"

    def run(self, question: str, context: dict[str, Any], runtime_state: Any) -> dict[str, Any]:
        self.bind(runtime_state)
        symbol = self._target_symbol(question, context)
        if not symbol:
            return self.ok("没有识别到明确的函数名，请使用类似 login() 的写法。", [], [], "未识别函数目标。", tools_used=["ast.parse"], trace_observation={"target": ""})

        matches = self._find_functions(symbol)
        if not matches:
            return self.ok(f"没有在当前项目中找到函数 {symbol}() 的定义。", [], [], "函数定义搜索无结果。", tools_used=["ast.parse"], trace_observation={"target": symbol, "matches": 0})

        match = matches[0]
        path = match["path"]
        node = match["node"]
        snippet = self._snippet_by_lines(path, node.lineno, getattr(node, "end_lineno", node.lineno))
        returns = self._returns(node)
        calls = self._calls_in_node(node)
        answer = "\n".join(
            [
                f"{symbol}() 定义在 {path}:{node.lineno}。",
                f"签名：{self._signature(node)}",
                f"参数：{', '.join(self._params(node)) or '无'}",
                f"返回值：{'; '.join(returns) if returns else '没有显式 return'}",
                f"内部调用：{', '.join(dict.fromkeys(calls)) if calls else '未发现函数调用'}",
                "",
                "代码片段：",
                f"```python\n{snippet}\n```",
            ]
        )
        evidence = [
            f"{path}:{node.lineno} 定义 {symbol}()",
            f"参数: {', '.join(self._params(node)) or '无'}",
            f"返回路径: {'; '.join(returns) if returns else '无显式 return'}",
        ]
        if calls:
            evidence.append(f"调用关系: {', '.join(dict.fromkeys(calls))}")
        return self.ok(
            answer,
            [path],
            evidence,
            "基于 AST 定位函数定义、参数、返回值、调用关系和代码片段。",
            tools_used=["list_files", "ast.parse", "ast.walk", "read_file"],
            observations={
                "target": symbol,
                "definition": {"path": path, "line": node.lineno},
                "signature": self._signature(node),
                "returns": returns,
                "calls": calls,
                "snippet": snippet,
            },
            trace_observation={"target": symbol, "definition": f"{path}:{node.lineno}", "files_read": [path], "calls": calls},
        )

    def _target_symbol(self, question: str, context: dict[str, Any]) -> str:
        match = re.search(r"`?([A-Za-z_][A-Za-z0-9_]*)\s*\(\)`?", question)
        if match:
            return match.group(1)
        symbols = context.get("target_symbols") or []
        return symbols[0] if symbols else ""

    def _find_functions(self, symbol: str) -> list[dict[str, Any]]:
        matches: list[dict[str, Any]] = []
        for path in self.files():
            try:
                tree = self.ast(path)
            except Exception:
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == symbol:
                    matches.append({"path": path, "node": node})
        return matches

    def _signature(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        return f"{node.name}({', '.join(self._params(node))})"

    def _params(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
        params = []
        for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
            params.append(arg.arg if arg.annotation is None else f"{arg.arg}: {ast.unparse(arg.annotation)}")
        if node.args.vararg:
            params.append("*" + node.args.vararg.arg)
        if node.args.kwarg:
            params.append("**" + node.args.kwarg.arg)
        return params

    def _returns(self, node: ast.AST) -> list[str]:
        values = []
        for child in ast.walk(node):
            if isinstance(child, ast.Return):
                values.append("None" if child.value is None else ast.unparse(child.value))
        return values

    def _calls_in_node(self, node: ast.AST) -> list[str]:
        calls = []
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                name = self._call_name(child.func)
                if name:
                    calls.append(name)
        return calls
