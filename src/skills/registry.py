from __future__ import annotations

from typing import Any

from .code_edit_skill import CodeEditSkill
from .code_fix_skill import CodeFixSkill
from .dependency_skill import DependencyTraceSkill
from .entrypoint_skill import EntrypointSkill
from .function_skill import FunctionExplainSkill
from .memory_skill import MemoryRecallSkill
from .problem_skill import ProblemTraceSkill
from .project_skill import ProjectOverviewSkill


class SkillRouter:
    def __init__(self) -> None:
        self.skills = {
            "project_overview": ProjectOverviewSkill(),
            "function_analysis": FunctionExplainSkill(),
            "entrypoint_analysis": EntrypointSkill(),
            "dependency_trace": DependencyTraceSkill(),
            "memory_recall": MemoryRecallSkill(),
            "problem_trace": ProblemTraceSkill(),
            "code_edit": CodeEditSkill(),
            "code_fix": CodeFixSkill(),
        }

    def skill_name(self, task_type: str) -> str:
        return (self.skills.get(task_type) or self.skills["project_overview"]).name

    def dispatch(self, question: str, plan: dict[str, Any], runtime_state: Any) -> dict[str, Any]:
        task_type = plan.get("task_type", "project_overview")
        skill = self.skills.get(task_type) or self.skills["project_overview"]
        context = {
            "plan": plan,
            "target_symbols": plan.get("target_symbols", []),
            "search_tokens": plan.get("search_tokens", []),
            "candidate_files_hint": plan.get("candidate_files_hint", []),
        }
        result = skill.run(question, context, runtime_state)
        result["skill"] = skill.name
        result["planner_type"] = task_type
        return result
