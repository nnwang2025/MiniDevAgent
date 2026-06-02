from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..llm_client import LLMClient
from ..prompts.planner_prompt import PLANNER_SYSTEM_PROMPT

TASK_TYPES = {
    "project_overview",
    "function_analysis",
    "entrypoint_analysis",
    "dependency_trace",
    "memory_recall",
    "problem_trace",
    "code_edit",
    "code_fix",
}

# Fast-path keyword patterns — used as cache, not as primary decision maker.
RULE_PATTERNS: list[tuple[list[str], str, str]] = [
    # (keywords, task_type, reason_template)
    # NOTE: Order matters — more specific patterns must come before broader ones.

    # ── Multi-source data (before code_edit/code_fix to avoid keyword overlap) ──
    (
        ["谁修的", "谁改的", "谁提交的", "哪个 commit", "谁加的",
         "什么时候改", "什么时候加", "为什么改", "为什么删", "周报",
         "这个决定", "谁决定的", "讨论记录", "群聊", "说过",
         "commit", "做了什么", "修了什么", "改了什么"],
        "memory_recall",
        "问题询问历史决策、代码变更历史或团队讨论记录。",
    ),
    (
        ["issue", "bug 记录", "报错记录", "出了什么问题", "故障排查",
         "部署日志", "上线记录", "生产环境", "staging 环境",
         "出现过什么错误", "发生过什么故障"],
        "problem_trace",
        "问题询问故障记录、部署问题或历史 bug 追踪。",
    ),

    # ── Code operations ──
    (
        ["修复", "修一下", "报错", "异常", "traceback",
         "fix", "error", "exception"],
        "code_fix",
        "问题包含错误/traceback 关键词，进入代码修复流程。",
    ),
    (
        ["修改代码", "增加功能", "删除代码", "重构代码", "优化代码",
         "添加校验", "异常处理", "change code", "edit code", "refactor code",
         "optimize code"],
        "code_edit",
        "问题要求修改/优化/重构代码，进入代码编辑流程。",
    ),

    # ── History / memory ──
    (
        ["之前", "以前", "后来", "历史", "记得", "什么时候",
         "为什么这样", "谁提过"],
        "memory_recall",
        "问题询问历史决策或协作记录，进入冷记忆召回。",
    ),

    # ── Code understanding ──
    (
        ["入口", "启动", "怎么启动", "执行入口", "启动流程",
         "main", "entrypoint"],
        "entrypoint_analysis",
        "问题询问代码执行入口或启动方式。",
    ),
    (
        ["谁调用", "被谁调用", "调用谁", "初始化", "依赖",
         "在哪里初始化", "哪里初始化"],
        "dependency_trace",
        "问题询问调用方、初始化位置或依赖关系。",
    ),
    (
        ["项目结构", "整体架构", "模块划分", "核心模块", "关键函数",
         "代码库结构", "这个项目是什么", "这个项目干嘛",
         "有哪些模块", "项目组成"],
        "project_overview",
        "问题询问项目用途、整体结构或核心模块。",
    ),
]


@dataclass
class PlannerAgent:
    """LLM-first task planner with rule-based fallback/cache.

    Primary: LLM with structured prompt (classify + decompose subtasks).
    Fallback: Rule-based keyword matching (fast, zero-cost cache hit).
    """

    llm: LLMClient = field(default_factory=LLMClient)

    def plan(self, question: str) -> dict[str, Any]:
        """Plan the task. LLM-first, rule fallback."""

        # 1. Try LLM (primary decision maker)
        llm_plan = self._plan_with_llm(question)
        if llm_plan and llm_plan.get("confidence", 0) >= 0.6:
            return self._finalize(llm_plan, "llm")

        # 2. Fallback: rule-based matching
        rule_plan = self._plan_with_rules(question)
        if rule_plan:
            return self._finalize(rule_plan, "rule")

        # 3. Last resort: function analysis if symbol pattern detected, else project overview
        if re.search(r"\b[A-Za-z_][A-Za-z0-9_]*\s*\(\)", question):
            return self._finalize(
                self._make_plan(
                    "function_analysis",
                    self._semantic_tokens(question),
                    [],
                    self._symbols(question),
                    "规则兜底：检测到函数调用模式。",
                ),
                "fallback",
            )

        return self._finalize(
            self._make_plan(
                "project_overview",
                self._semantic_tokens(question),
                [],
                [],
                "未命中任何规则或 LLM 分类，默认按项目概览处理。",
            ),
            "fallback",
        )

    # ── LLM planning ─────────────────────────────────────────────

    def _plan_with_llm(self, question: str) -> dict[str, Any] | None:
        """Primary planner: use LLM with structured prompt."""
        parsed = self.llm.chat_json(PLANNER_SYSTEM_PROMPT, question, timeout=30.0)
        if not parsed:
            return None
        task_type = parsed.get("task_type", "")
        if task_type not in TASK_TYPES:
            return None
        # Normalize confidence
        confidence = float(parsed.get("confidence", 0.5))
        return {
            "task_type": task_type,
            "search_tokens": self._dedupe(parsed.get("search_tokens", [])),
            "candidate_files_hint": self._dedupe(parsed.get("candidate_files_hint", [])),
            "target_symbols": self._dedupe(parsed.get("target_symbols", [])),
            "sub_tasks": self._dedupe(parsed.get("sub_tasks", [])),
            "complexity": parsed.get("complexity", "simple"),
            "reason": parsed.get("reason", "LLM 分类结果。"),
            "confidence": confidence,
        }

    # ── Rule-based planning (fallback) ───────────────────────────

    def _plan_with_rules(self, question: str) -> dict[str, Any] | None:
        """Rule-based fallback: fast keyword matching."""
        lower = question.lower()
        symbols = self._symbols(question)

        for keywords, task_type, reason in RULE_PATTERNS:
            if self._contains_any(question, keywords) or any(kw in lower for kw in keywords):
                return self._make_plan(
                    task_type,
                    self._semantic_tokens(question),
                    self._default_files(task_type),
                    symbols or self._dependency_symbols(question),
                    reason,
                )

        # Function call pattern
        if re.search(r"\b[A-Za-z_][A-Za-z0-9_]*\s*\(\)", question):
            return self._make_plan(
                "function_analysis",
                self._semantic_tokens(question),
                [],
                symbols,
                "问题包含函数名()，进入函数 AST 分析。",
            )

        return None

    # ── helpers ───────────────────────────────────────────────────

    def _finalize(self, plan: dict[str, Any], planner: str) -> dict[str, Any]:
        task_type = plan.get("task_type") if plan.get("task_type") in TASK_TYPES else "project_overview"
        return {
            "objective": plan.get("objective", ""),
            "task_type": task_type,
            "complexity": plan.get("complexity", "simple"),
            "search_tokens": self._dedupe(plan.get("search_tokens", [])),
            "candidate_files_hint": self._dedupe(plan.get("candidate_files_hint", [])),
            "target_symbols": self._dedupe(plan.get("target_symbols", [])),
            "sub_tasks": self._dedupe(plan.get("sub_tasks", [])),
            "reason": plan.get("reason", "基于规则/LLM 分类。"),
            "confidence": float(plan.get("confidence", 0.7)),
            "planner": planner,
        }

    def _make_plan(
        self,
        task_type: str,
        tokens: list[str],
        files: list[str],
        symbols: list[str],
        reason: str,
    ) -> dict[str, Any]:
        return {
            "task_type": task_type,
            "search_tokens": self._dedupe(tokens),
            "candidate_files_hint": self._dedupe(files),
            "target_symbols": self._dedupe(symbols),
            "sub_tasks": [],
            "complexity": "simple",
            "reason": reason,
            "confidence": 0.65,  # rule-based gets moderate confidence
        }

    def _default_files(self, task_type: str) -> list[str]:
        """Default candidate files per task type."""
        return {
            "entrypoint_analysis": ["main.py", "app.py", "routes.py", "__init__.py"],
        }.get(task_type, [])

    def _semantic_tokens(self, question: str) -> list[str]:
        tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", question)
        if "数据库" in question or "database" in question.lower():
            tokens.extend(["database", "DatabaseConnection", "get_default_connection"])
        if any(kw in question for kw in ["登录", "认证", "auth", "用户名", "密码"]):
            tokens.extend(["auth", "AuthService", "login"])
        if any(kw in question for kw in ["入口", "启动"]):
            tokens.extend(["main", "bootstrap", "create_app", "uvicorn"])
        return self._dedupe(tokens)

    def _dependency_symbols(self, question: str) -> list[str]:
        if "数据库" in question or "database" in question.lower():
            return ["get_default_connection", "DatabaseConnection"]
        return []

    def _symbols(self, question: str) -> list[str]:
        symbols = re.findall(r"`?([A-Za-z_][A-Za-z0-9_]*)\s*\(\)`?", question)
        symbols.extend(re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", question))
        symbols.extend(re.findall(r"\b([A-Z][A-Za-z0-9_]+)\b", question))
        return self._dedupe(symbols)

    def _contains_any(self, text: str, terms: list[str]) -> bool:
        return any(term in text for term in terms)

    def _dedupe(self, values: Any) -> list[str]:
        if not isinstance(values, list):
            return []
        result: list[str] = []
        for value in values:
            item = str(value).strip()
            if item and item not in result:
                result.append(item)
        return result
