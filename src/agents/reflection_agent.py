from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..llm_client import LLMClient

REFLECTION_SYSTEM_PROMPT = """你是 MiniDevAgent 的 Reflection Agent。你的职责是审查任务执行结果，判断是否需要修正。

你需要像一个严格的代码审查者一样思考：
1. 任务目标是否达成？如果没有，根因是什么？
2. 执行过程中是否有遗漏的步骤或边界情况？
3. 生成的代码改动是否引入了新问题？
4. 是否有未覆盖的测试场景？
5. 下一步应该做什么？

## 输出 JSON 格式

{
  "success": true/false,
  "issues_found": ["发现的问题"],
  "root_causes": ["问题根因"],
  "coverage_gaps": ["遗漏了什么"],
  "suggested_fixes": ["修正建议"],
  "confidence": 0.0-1.0,
  "should_retry": true/false,
  "refinement_plan": ["修正后要执行的步骤"]
}

## 判断标准

- 代码理解类任务（project_overview, function_analysis, entrypoint_analysis, dependency_trace）：
  判断信息是否完整、准确、有引用来源。缺失关键信息 → should_retry=true
- 代码编辑类任务（code_edit, code_fix）：
  判断修改是否正确、是否考虑边界情况、测试是否通过。
  测试失败或有明显遗漏 → should_retry=true
- 记忆召回类任务（memory_recall, problem_trace）：
  判断是否找到了相关信息。信息不足 → should_retry=true（扩大检索范围）

- confidence >= 0.85 且 success=true → should_retry=false
- confidence < 0.5 → should_retry=true
- 已经重试 3 次 → should_retry=false（接受当前结果）
"""


@dataclass
class ReflectionResult:
    """Structured reflection output — the key differentiator from vanilla ReAct."""
    success: bool
    issues_found: list[str] = field(default_factory=list)
    root_causes: list[str] = field(default_factory=list)
    coverage_gaps: list[str] = field(default_factory=list)
    suggested_fixes: list[str] = field(default_factory=list)
    confidence: float = 1.0
    should_retry: bool = False
    refinement_plan: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "issues_found": self.issues_found,
            "root_causes": self.root_causes,
            "coverage_gaps": self.coverage_gaps,
            "suggested_fixes": self.suggested_fixes,
            "confidence": self.confidence,
            "should_retry": self.should_retry,
            "refinement_plan": self.refinement_plan,
        }


class ReflectionAgent:
    """Explicit reflection stage — not implicit in Thought, but a dedicated agent.

    This is what differentiates PERR from ReAct:
    - ReAct: Thought → Action → Observation (implicit evaluation mixed in Thought)
    - PERR: Plan → Execute → Verify → Reflect (explicit, structured evaluation)
    """

    def __init__(self) -> None:
        self.llm = LLMClient()

    def reflect(
        self,
        question: str,
        plan: dict[str, Any],
        execution_result: dict[str, Any],
        verification_result: dict[str, Any] | None = None,
        retry_count: int = 0,
        max_retries: int = 3,
    ) -> ReflectionResult:
        """Reflect on execution results and decide whether to refine.

        Args:
            question: Original user question.
            plan: The plan that was executed.
            execution_result: What the skill produced.
            verification_result: Verification output (compile, pytest, etc.).
            retry_count: Current retry round (0-indexed).
            max_retries: Maximum retries allowed.
        """
        # Fast path: if it's a simple task with no issues, skip LLM reflection
        if self._is_trivially_successful(plan, execution_result, verification_result):
            return ReflectionResult(
                success=True,
                confidence=0.9,
                should_retry=False,
            )

        # If LLM is not available, use heuristic reflection
        if not self.llm.enabled:
            return self._heuristic_reflection(
                question, plan, execution_result, verification_result, retry_count, max_retries
            )

        # LLM-driven reflection
        return self._llm_reflection(
            question, plan, execution_result, verification_result, retry_count, max_retries
        )

    def _is_trivially_successful(
        self,
        plan: dict[str, Any],
        execution_result: dict[str, Any],
        verification_result: dict[str, Any] | None,
    ) -> bool:
        """Quick check: is this an obviously successful execution?"""
        task_type = plan.get("task_type", "")
        # Simple understanding tasks with evidence are usually fine
        if task_type in {"project_overview", "function_analysis", "entrypoint_analysis"}:
            files = execution_result.get("files_used", [])
            evidence = execution_result.get("evidence", [])
            if files and evidence:
                return True
        # Memory recall with records found
        if task_type in {"memory_recall", "problem_trace"}:
            records = execution_result.get("related_records", [])
            if records:
                return True
        # Code tasks with successful verification
        if task_type in {"code_edit", "code_fix"}:
            if verification_result and verification_result.get("success"):
                return True
        return False

    def _heuristic_reflection(
        self,
        question: str,
        plan: dict[str, Any],
        execution_result: dict[str, Any],
        verification_result: dict[str, Any] | None,
        retry_count: int,
        max_retries: int,
    ) -> ReflectionResult:
        """Rule-based reflection when LLM is unavailable."""
        task_type = plan.get("task_type", "")
        issues: list[str] = []
        gaps: list[str] = []

        # Check for empty results
        if not execution_result.get("files_used"):
            gaps.append("未找到相关文件")
        if not execution_result.get("evidence"):
            gaps.append("未收集到有效证据")
        if not execution_result.get("answer"):
            issues.append("未能生成回答")

        # Check verification for code tasks
        if task_type in {"code_edit", "code_fix"}:
            if verification_result:
                if not verification_result.get("success"):
                    issues.append(f"验证失败: {verification_result}")
                if verification_result.get("compileall", {}).get("returncode"):
                    issues.append("编译验证未通过")
            else:
                gaps.append("未执行验证步骤")

        success = len(issues) == 0 and len(gaps) <= 1
        confidence = 0.8 if success else max(0.3, 0.8 - 0.2 * len(issues) - 0.1 * len(gaps))
        should_retry = not success and retry_count < max_retries

        return ReflectionResult(
            success=success,
            issues_found=issues,
            root_causes=["规则引擎判断" for _ in issues],
            coverage_gaps=gaps,
            suggested_fixes=["扩大搜索范围" if not execution_result.get("files_used") else "增加验证步骤"],
            confidence=confidence,
            should_retry=should_retry,
            refinement_plan=(
                ["使用更广泛的关键词搜索", "检查更多候选文件", "重新执行并验证"]
                if should_retry else []
            ),
        )

    def _llm_reflection(
        self,
        question: str,
        plan: dict[str, Any],
        execution_result: dict[str, Any],
        verification_result: dict[str, Any] | None,
        retry_count: int,
        max_retries: int,
    ) -> ReflectionResult:
        """LLM-driven deep reflection."""
        user_prompt = self._build_reflection_prompt(
            question, plan, execution_result, verification_result, retry_count, max_retries
        )
        result = self.llm.chat_json(REFLECTION_SYSTEM_PROMPT, user_prompt, timeout=30.0)

        if not result:
            return self._heuristic_reflection(
                question, plan, execution_result, verification_result, retry_count, max_retries
            )

        return ReflectionResult(
            success=bool(result.get("success", False)),
            issues_found=self._as_str_list(result.get("issues_found", [])),
            root_causes=self._as_str_list(result.get("root_causes", [])),
            coverage_gaps=self._as_str_list(result.get("coverage_gaps", [])),
            suggested_fixes=self._as_str_list(result.get("suggested_fixes", [])),
            confidence=float(result.get("confidence", 0.5)),
            should_retry=bool(result.get("should_retry", False)) and retry_count < max_retries,
            refinement_plan=self._as_str_list(result.get("refinement_plan", [])),
        )

    def _build_reflection_prompt(
        self,
        question: str,
        plan: dict[str, Any],
        execution_result: dict[str, Any],
        verification_result: dict[str, Any] | None,
        retry_count: int,
        max_retries: int,
    ) -> str:
        parts = [
            f"## 用户问题\n{question}",
            f"## 任务计划\n类型: {plan.get('task_type', 'unknown')}",
        ]
        if plan.get("sub_tasks"):
            parts.append(f"子任务: {plan.get('sub_tasks')}")
        parts.append(f"规划理由: {plan.get('reason', '')}")
        parts.append(f"复杂度: {plan.get('complexity', 'simple')}")
        parts.append("")
        parts.append("## 执行结果")
        parts.append(f"回答摘要: {str(execution_result.get('answer', ''))[:500]}")
        parts.append(f"使用的文件: {execution_result.get('files_used', [])}")
        parts.append(f"证据: {execution_result.get('evidence', [])[:5]}")
        parts.append(f"使用的工具: {execution_result.get('tools_used', [])}")
        if execution_result.get("pending_patch"):
            patch = execution_result["pending_patch"]
            parts.append(f"Patch 文件: {patch.get('file', '')}")
            parts.append(f"Patch 摘要: {patch.get('summary', '')}")
        parts.append("")
        if verification_result:
            parts.append(f"## 验证结果\n{verification_result}")
        parts.append("")
        parts.append(f"## 重试信息\n当前第 {retry_count + 1} 轮，最多 {max_retries} 轮。")
        parts.append("\n请审查以上执行结果并给出结构化反思。")
        return "\n".join(parts)

    def _as_str_list(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if str(item).strip()]
