from __future__ import annotations

import ast
import re
from typing import Any

from ..mcp.registry import create_mcp_client
from ..runtime.patch_generator import PatchGenerator
from .base import BaseSkill


class CodeEditSkill(BaseSkill):
    name = "CodeEditSkill"

    def run(self, question: str, context: dict[str, Any], runtime_state: Any) -> dict[str, Any]:
        self.bind(runtime_state)
        mcp = create_mcp_client(self.project_path)
        target = self._target_symbol(question, context)
        locate = mcp.call("code_analysis", "find_symbol", symbol=target) if target else {"ok": False, "data": {"matches": []}}
        matches = locate.get("data", {}).get("matches", []) if locate.get("ok") else []
        if not matches:
            return self.ok("没有定位到可修改的目标函数/类，因此未生成 patch。", [], [], "CodeEditSkill dry-run 未定位到目标。", tools_used=["code_analysis.find_symbol"], trace_observation={"target": target, "matches": 0})

        match = matches[0]
        file_path = match["path"]
        read = mcp.call("filesystem", "read_file", path=file_path)
        if not read.get("ok"):
            return self.ok(f"读取目标文件失败：{read.get('error')}", [file_path], [], "MCP filesystem.read_file 失败。", tools_used=["filesystem.read_file"], trace_observation={"error": read.get("error")})

        old_content = read["data"]["content"]
        new_content, summary = self._edit_content(question, old_content, target)
        if new_content == old_content:
            return self.ok("已定位目标，但当前规则无法安全生成修改 patch。", [file_path], [], "未生成变更。", tools_used=["code_analysis.find_symbol", "filesystem.read_file"], trace_observation={"target": target, "file": file_path, "patch": False})

        patch = PatchGenerator().generate(file_path, old_content, new_content, summary)
        answer = "\n".join(
            [
                "已生成 dry-run patch，尚未写入文件。",
                "如需应用，请在 CLI 中输入 yes 确认。",
                "",
                patch["diff"],
            ]
        )
        return self.ok(
            answer,
            [file_path],
            [f"{file_path}: 生成 unified diff，未自动写入。"],
            "READ -> ANALYZE -> PATCH preview；等待 human-in-the-loop 确认。",
            tools_used=["code_analysis.find_symbol", "filesystem.read_file", "patch_generator.generate"],
            observations={"target": target, "patch": patch},
            trace_observation={"target": target, "file": file_path, "dry_run": True, "diff_lines": len(patch["diff"].splitlines())},
            pending_patch=patch,
        )

    def _target_symbol(self, question: str, context: dict[str, Any]) -> str:
        match = re.search(r"`?([A-Za-z_][A-Za-z0-9_]*)\s*\(\)`?", question)
        if match:
            return match.group(1)
        symbols = context.get("target_symbols") or []
        if symbols:
            return symbols[0]
        if "bootstrap" in question.lower():
            return "bootstrap"
        if "login" in question.lower() or "登录" in question or "密码" in question or "用户名" in question:
            return "login"
        return ""

    def _edit_content(self, question: str, content: str, symbol: str) -> tuple[str, str]:
        if symbol == "login" and ("密码" in question or "用户名" in question or "校验" in question):
            old = '''    def login(self, username: str, password: str) -> bool:
        user = self.user_store.get(username)
        return bool(user and user.get("password") == password)
'''
            new = '''    def login(self, username: str, password: str) -> bool:
        if not username or not password:
            return False
        user = self.user_store.get(username)
        return bool(user and user.get("password") == password)
'''
            if old in content:
                return content.replace(old, new, 1), "给 login() 增加空用户名和空密码校验。"
        if symbol == "bootstrap" and ("优化" in question or "重构" in question or "optimize" in question.lower()):
            old = '''def bootstrap() -> str:
    connection = get_default_connection()
    status = connection.connect()
    auth = AuthService(user_store={"alice": {"password": "secret"}})
    user = {"username": normalize_username("Alice"), "password": "secret"}
    authorized = auth.login(user["username"], user["password"])
    if authorized and is_authenticated(user):
        return format_message(status + " and user authenticated.")
    return format_message("Authentication failed.")
'''
            new = '''def bootstrap() -> str:
    connection = get_default_connection()
    status = connection.connect()
    user_store = {"alice": {"password": "secret"}}
    auth = AuthService(user_store=user_store)
    user = {"username": normalize_username("Alice"), "password": "secret"}
    authorized = auth.login(user["username"], user["password"])

    if not authorized or not is_authenticated(user):
        return format_message("Authentication failed.")
    return format_message(status + " and user authenticated.")
'''
            if old in content:
                return content.replace(old, new, 1), "优化 bootstrap() 的局部变量命名和失败分支。"
        return content, ""
