from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from ..llm_client import LLMClient


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


@dataclass
class PlannerAgent:
    def __post_init__(self) -> None:
        self.llm = LLMClient()

    def plan(self, question: str) -> dict[str, Any]:
        rule_plan = self._plan_with_rules(question)
        if rule_plan:
            return self._finalize(question, rule_plan, "rule")
        llm_plan = self._plan_with_llm(question)
        if llm_plan:
            return self._finalize(question, llm_plan, "llm")
        return self._finalize(question, self._make_plan("project_overview", self._semantic_tokens(question), [], [], "未命中更具体规则，默认按项目概览处理。"), "fallback")

    def _plan_with_rules(self, question: str) -> dict[str, Any] | None:
        lower = question.lower()
        symbols = self._symbols(question)

        if self._contains_any(question, ["修复", "修一下", "报错", "错误", "失败", "异常", "traceback"]) or any(term in lower for term in ["fix", "traceback", "error", "exception"]):
            return self._make_plan("code_fix", self._semantic_tokens(question), [], symbols, "问题要求修复 bug/异常/traceback，进入代码修复技能。")

        if self._contains_any(question, ["修改", "增加", "删除", "重构", "优化", "添加", "校验", "日志", "异常处理"]) or any(term in lower for term in ["change", "edit", "refactor", "optimize", "add", "delete"]):
            return self._make_plan("code_edit", self._semantic_tokens(question), [], symbols, "问题要求修改/优化/重构代码，进入代码编辑技能。")

        if "bug" in lower:
            return self._make_plan("problem_trace", self._semantic_tokens(question), [], symbols, "问题询问 bug 记录，但未明确要求改代码，进入问题追踪技能。")

        if self._contains_any(question, ["之前", "以前", "后来", "为什么改", "谁提过", "什么时候", "历史", "记得"]):
            return self._make_plan("memory_recall", self._semantic_tokens(question), [], symbols, "问题询问历史原因、时间或协作记忆，进入冷记忆召回技能。")

        if self._contains_any(question, ["入口", "启动", "怎么启动", "执行入口", "启动流程"]) or lower.strip() in {"main", "entrypoint"}:
            return self._make_plan("entrypoint_analysis", ["main", "bootstrap", "create_app", "FastAPI", "uvicorn"], ["main.py", "app.py", "routes.py"], symbols or ["main", "bootstrap", "create_app"], "问题询问代码执行入口或启动方式。")

        if re.search(r"\b[A-Za-z_][A-Za-z0-9_]*\s*\(\)", question):
            return self._make_plan("function_analysis", self._semantic_tokens(question), [], symbols, "问题包含函数名()，进入函数 AST 分析。")

        if self._contains_any(question, ["谁调用", "被谁调用", "调用谁", "初始化", "依赖", "在哪里初始化", "哪里初始化"]):
            return self._make_plan("dependency_trace", self._semantic_tokens(question), [], symbols or self._dependency_symbols(question), "问题询问调用方、初始化位置或依赖关系。")

        if self._contains_any(question, ["项目", "整体", "结构", "模块", "核心模块", "关键函数", "代码库", "这个项目是什么", "这个项目干嘛"]):
            return self._make_plan("project_overview", ["project", "architecture", "module", "entry", "core"], [], symbols, "问题询问项目用途、整体结构或核心模块。")

        return None

    def _plan_with_llm(self, question: str) -> dict[str, Any] | None:
        system = (
            "你是代码工程 Agent 的规划器。只返回 JSON。"
            "task_type 必须是 project_overview/function_analysis/entrypoint_analysis/"
            "dependency_trace/memory_recall/problem_trace/code_edit/code_fix 之一。"
        )
        parsed = self.llm.chat_json(system, question)
        return parsed if parsed and parsed.get("task_type") in TASK_TYPES else None

    def _finalize(self, question: str, plan: dict[str, Any], planner: str) -> dict[str, Any]:
        task_type = plan.get("task_type") if plan.get("task_type") in TASK_TYPES else "project_overview"
        return {
            "objective": question,
            "task_type": task_type,
            "search_tokens": self._dedupe(plan.get("search_tokens", [])),
            "candidate_files_hint": self._dedupe(plan.get("candidate_files_hint", [])),
            "target_symbols": self._dedupe(plan.get("target_symbols", [])),
            "reason": plan.get("reason", "基于规则识别问题意图。"),
            "planner": planner,
        }

    def _make_plan(self, task_type: str, tokens: list[str], files: list[str], symbols: list[str], reason: str) -> dict[str, Any]:
        return {"task_type": task_type, "search_tokens": self._dedupe(tokens), "candidate_files_hint": self._dedupe(files), "target_symbols": self._dedupe(symbols), "reason": reason}

    def _semantic_tokens(self, question: str) -> list[str]:
        tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", question)
        if "数据库" in question or "database" in question.lower():
            tokens.extend(["database", "DatabaseConnection", "get_default_connection"])
        if "登录" in question or "认证" in question or "auth" in question.lower() or "用户名" in question or "密码" in question:
            tokens.extend(["auth", "AuthService", "login"])
        if "入口" in question or "启动" in question:
            tokens.extend(["main", "bootstrap", "create_app", "uvicorn"])
        return self._dedupe(tokens)

    def _dependency_symbols(self, question: str) -> list[str]:
        return ["get_default_connection", "DatabaseConnection"] if "数据库" in question or "database" in question.lower() else []

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
