from __future__ import annotations

import re
from typing import Any

from ..mcp.registry import create_mcp_client
from ..runtime.patch_generator import PatchGenerator
from .code_edit_skill import CodeEditSkill


class CodeFixSkill(CodeEditSkill):
    name = "CodeFixSkill"

    def run(self, question: str, context: dict[str, Any], runtime_state: Any) -> dict[str, Any]:
        self.bind(runtime_state)
        mcp = create_mcp_client(self.project_path)
        target = self._target_from_traceback(question) or self._target_symbol(question, context)
        if not target:
            return self.ok("没有从 traceback/问题描述中定位到可修复的函数，因此未生成 patch。", [], [], "CodeFixSkill 未定位目标。", tools_used=["code_analysis.find_symbol"], trace_observation={"target": ""})

        locate = mcp.call("code_analysis", "find_symbol", symbol=target)
        matches = locate.get("data", {}).get("matches", []) if locate.get("ok") else []
        if not matches:
            return self.ok(f"没有找到 {target} 的定义，无法生成修复 patch。", [], [], "CodeFixSkill find_symbol 无结果。", tools_used=["code_analysis.find_symbol"], trace_observation={"target": target, "matches": 0})

        file_path = matches[0]["path"]
        read = mcp.call("filesystem", "read_file", path=file_path)
        if not read.get("ok"):
            return self.ok(f"读取目标文件失败：{read.get('error')}", [file_path], [], "MCP filesystem.read_file 失败。", tools_used=["filesystem.read_file"], trace_observation={"error": read.get("error")})

        old_content = read["data"]["content"]
        new_content, summary = self._fix_content(question, old_content, target)
        if new_content == old_content:
            return self.ok("已分析 traceback/错误描述，但没有足够证据生成安全修复。", [file_path], [], "未生成修复 patch。", tools_used=["code_analysis.find_symbol", "filesystem.read_file"], trace_observation={"target": target, "file": file_path, "patch": False})

        patch = PatchGenerator().generate(file_path, old_content, new_content, summary)
        answer = "\n".join(
            [
                "已生成修复 patch 预览，尚未写入文件。",
                "验证计划：应用后运行 python -m compileall；如存在 tests，再运行 pytest。",
                "如需应用，请在 CLI 中输入 yes 确认。",
                "",
                patch["diff"],
            ]
        )
        return self.ok(
            answer,
            [file_path],
            [f"{file_path}: 根据 traceback/错误描述生成修复 diff，未自动写入。"],
            "ANALYZE traceback -> LOCATE code -> PATCH preview -> verification plan。",
            tools_used=["code_analysis.find_symbol", "filesystem.read_file", "patch_generator.generate"],
            observations={"target": target, "patch": patch, "verification_plan": ["python -m compileall", "pytest if exists"]},
            trace_observation={"target": target, "file": file_path, "dry_run": True, "verification_plan": ["compileall", "pytest if exists"]},
            pending_patch=patch,
        )

    def _target_from_traceback(self, text: str) -> str:
        matches = re.findall(r'in ([A-Za-z_][A-Za-z0-9_]*)\n|in ([A-Za-z_][A-Za-z0-9_]*)\r?\n', text)
        flattened = [item for pair in matches for item in pair if item]
        if flattened:
            return flattened[-1]
        function_names = re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\(\)", text)
        return function_names[0] if function_names else ""

    def _fix_content(self, question: str, content: str, symbol: str) -> tuple[str, str]:
        if symbol == "login" and any(token in question.lower() for token in ["none", "empty", "password", "用户名", "密码", "空"]):
            return self._edit_content("给 login() 增加空用户名和空密码校验", content, "login")
        return content, ""
