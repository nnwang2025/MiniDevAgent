from __future__ import annotations

import re
from typing import Any

from ..llm_client import LLMClient
from ..mcp.registry import create_mcp_client
from ..runtime.patch_generator import PatchGenerator
from .base import BaseSkill

CODE_EDIT_SYSTEM_PROMPT = """你是代码编辑专家。给定一个文件的完整内容和用户的修改请求，你需要返回修改后的完整文件内容。

## 要求
1. 保持文件编码和原有格式风格
2. 只修改用户要求的部分，其他代码保持不变
3. 缩进、空行、注释风格保持与原文一致
4. 如果是添加校验逻辑，注意边界条件的处理
5. 如果是修复 bug，确保不引入新问题

## 输出格式 (JSON)
{
  "new_content": "修改后的完整文件内容",
  "summary": "用中文简要描述做了什么修改",
  "changes": ["修改点1", "修改点2"]
}

如果修改请求无法安全实现，返回：
{
  "new_content": "",
  "summary": "无法安全修改的原因",
  "changes": []
}
"""


class CodeEditSkill(BaseSkill):
    """LLM-driven code editing skill.

    Primary path: LLM understands the edit intent and generates modified code.
    Fallback path: Rule-based for known patterns when LLM is unavailable.
    """

    name = "CodeEditSkill"

    def __init__(self) -> None:
        self.llm = LLMClient()

    def run(self, question: str, context: dict[str, Any], runtime_state: Any) -> dict[str, Any]:
        self.bind(runtime_state)
        mcp = create_mcp_client(self.project_path)

        # 1. Identify target symbol and file
        target = self._target_symbol(question, context)
        if target:
            locate = mcp.call("code_analysis", "find_symbol", symbol=target)
            matches = locate.get("data", {}).get("matches", []) if locate.get("ok") else []
        else:
            # No specific symbol — try to infer file from question
            matches = []
            candidates = context.get("candidate_files_hint", [])
            if candidates:
                matches = [{"path": candidates[0], "line": 1}]

        if not matches:
            return self.ok(
                "没有定位到可修改的目标函数/类，因此未生成 patch。请指定具体的函数名或文件。",
                [], [],
                "CodeEditSkill 未定位到目标。",
                tools_used=["code_analysis.find_symbol"],
                trace_observation={"target": target, "matches": 0},
            )

        match = matches[0]
        file_path = match["path"]
        read = mcp.call("filesystem", "read_file", path=file_path)
        if not read.get("ok"):
            return self.ok(
                f"读取目标文件失败：{read.get('error')}",
                [file_path], [],
                "MCP filesystem.read_file 失败。",
                tools_used=["filesystem.read_file"],
                trace_observation={"error": read.get("error")},
            )

        old_content = read["data"]["content"]

        # 2. Generate edit — LLM first, rule fallback
        new_content, summary, changes = self._generate_edit(question, file_path, old_content, target)

        if new_content == old_content or not new_content:
            return self.ok(
                "已定位目标，但无法安全生成修改 patch。" + (f" 原因: {summary}" if summary else ""),
                [file_path], [],
                "未生成变更。" if not summary else summary,
                tools_used=["code_analysis.find_symbol", "filesystem.read_file"],
                trace_observation={"target": target, "file": file_path, "patch": False, "reason": summary or "无有效变更"},
            )

        # 3. Generate diff
        patch = PatchGenerator().generate(file_path, old_content, new_content, summary)
        answer_lines = [
            "已生成 dry-run patch，尚未写入文件。",
            f"修改摘要: {summary}",
        ]
        if changes:
            answer_lines.append("")
            answer_lines.append("变更列表:")
            for change in changes:
                answer_lines.append(f"  - {change}")
        answer_lines.append("")
        answer_lines.append("如需应用，请在 CLI 中输入 yes 确认。")
        answer_lines.append("")
        answer_lines.append(patch["diff"])

        return self.ok(
            "\n".join(answer_lines),
            [file_path],
            [f"{file_path}: 生成 unified diff，等待 human-in-the-loop 确认。"],
            "READ -> ANALYZE -> LLM GENERATE -> PATCH preview；等待 human-in-the-loop 确认。",
            tools_used=["code_analysis.find_symbol", "filesystem.read_file", "llm.edit", "patch_generator.generate"],
            observations={"target": target, "patch": patch, "changes": changes},
            trace_observation={
                "target": target,
                "file": file_path,
                "dry_run": True,
                "diff_lines": len(patch["diff"].splitlines()),
                "changes": changes,
            },
            pending_patch=patch,
        )

    # ── target identification ────────────────────────────────────

    def _target_symbol(self, question: str, context: dict[str, Any]) -> str:
        """Extract the target symbol (function/class name) from the question."""
        # Explicit function() pattern
        match = re.search(r"`?([A-Za-z_][A-Za-z0-9_]*)\s*\(\)`?", question)
        if match:
            return match.group(1)
        # From plan context
        symbols = context.get("target_symbols") or []
        if symbols:
            return symbols[0]
        # Keyword heuristics
        lower = question.lower()
        if "bootstrap" in lower:
            return "bootstrap"
        if "login" in lower or "登录" in question or "密码" in question or "用户名" in question:
            return "login"
        if "auth" in lower or "认证" in question:
            return "authenticate"
        return ""

    # ── edit generation ──────────────────────────────────────────

    def _generate_edit(
        self, question: str, file_path: str, content: str, target: str
    ) -> tuple[str, str, list[str]]:
        """Generate edit. LLM primary, rule fallback."""
        # Try LLM first
        if self.llm.enabled:
            llm_result = self._llm_edit(question, file_path, content, target)
            if llm_result[0]:
                return llm_result

        # Rule-based fallback for known patterns
        return self._rule_edit(question, content, target)

    def _llm_edit(
        self, question: str, file_path: str, content: str, target: str
    ) -> tuple[str, str, list[str]]:
        """LLM-driven code edit."""
        user_prompt = (
            f"## 文件路径\n{file_path}\n\n"
            f"## 目标符号\n{target if target else '(未指定具体符号)'}\n\n"
            f"## 修改请求\n{question}\n\n"
            f"## 当前文件内容\n```python\n{content}\n```\n\n"
            f"请根据修改请求，返回修改后的完整文件内容。"
        )
        result = self.llm.chat_json(CODE_EDIT_SYSTEM_PROMPT, user_prompt, timeout=60.0)
        if not result:
            return "", "", []

        new_content = result.get("new_content", "")
        summary = result.get("summary", "")
        changes = result.get("changes", [])

        # Validate: new_content must be non-empty and different from original
        if not new_content or new_content == content:
            return "", summary or "LLM 未生成有效修改", []

        # Safety: ensure new_content is valid Python (best effort)
        try:
            import ast
            ast.parse(new_content)
        except SyntaxError as exc:
            return "", f"LLM 生成的代码有语法错误: {exc}", []

        return new_content, summary, changes if isinstance(changes, list) else []

    def _rule_edit(
        self, question: str, content: str, symbol: str
    ) -> tuple[str, str, list[str]]:
        """Rule-based fallback for known edit patterns."""
        # login() — add empty validation
        if symbol == "login" and ("密码" in question or "用户名" in question or "校验" in question or "validate" in question.lower()):
            old = (
                "    def login(self, username: str, password: str) -> bool:\n"
                "        user = self.user_store.get(username)\n"
                "        return bool(user and user.get(\"password\") == password)\n"
            )
            new = (
                "    def login(self, username: str, password: str) -> bool:\n"
                "        if not username or not password:\n"
                "            return False\n"
                "        user = self.user_store.get(username)\n"
                "        return bool(user and user.get(\"password\") == password)\n"
            )
            if old in content:
                return (
                    content.replace(old, new, 1),
                    "给 login() 增加空用户名和空密码校验。",
                    ["添加了 username/password 空值校验逻辑"],
                )

        # bootstrap() — optimize variable naming
        if symbol == "bootstrap" and ("优化" in question or "重构" in question or "optimize" in question.lower()):
            old = (
                "def bootstrap() -> str:\n"
                "    connection = get_default_connection()\n"
                "    status = connection.connect()\n"
                "    auth = AuthService(user_store={\"alice\": {\"password\": \"secret\"}})\n"
                "    user = {\"username\": normalize_username(\"Alice\"), \"password\": \"secret\"}\n"
                "    authorized = auth.login(user[\"username\"], user[\"password\"])\n"
                "    if authorized and is_authenticated(user):\n"
                "        return format_message(status + \" and user authenticated.\")\n"
                "    return format_message(\"Authentication failed.\")\n"
            )
            new = (
                "def bootstrap() -> str:\n"
                "    connection = get_default_connection()\n"
                "    status = connection.connect()\n"
                "    user_store = {\"alice\": {\"password\": \"secret\"}}\n"
                "    auth = AuthService(user_store=user_store)\n"
                "    user = {\"username\": normalize_username(\"Alice\"), \"password\": \"secret\"}\n"
                "    authorized = auth.login(user[\"username\"], user[\"password\"])\n"
                "\n"
                "    if not authorized or not is_authenticated(user):\n"
                "        return format_message(\"Authentication failed.\")\n"
                "    return format_message(status + \" and user authenticated.\")\n"
            )
            if old in content:
                return (
                    content.replace(old, new, 1),
                    "优化 bootstrap() 的局部变量命名和失败分支。",
                    ["提取 user_store 变量避免内联字面量", "改进条件判断结构为 early return 模式"],
                )

        return content, "", []
