from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..llm_client import LLMClient

VERIFY_SYSTEM_PROMPT = """你是代码验证专家。你需要验证一个代码修改是否符合用户意图、是否安全、是否完整。

## 验证维度
1. **意图匹配**: 修改是否实现了用户要求的功能？
2. **副作用检查**: 修改是否会影响其他代码（调用方、被调用方、测试）？
3. **边界完整性**: 是否处理了边界情况（空值、异常、并发等）？
4. **代码质量**: 修改是否符合项目风格？是否有明显的 bug？

## 输出格式 (JSON)
{
  "intent_match": true/false,
  "has_side_effects": true/false,
  "side_effect_details": ["受影响的调用方或依赖"],
  "boundary_issues": ["未处理的边界情况"],
  "quality_issues": ["代码质量问题"],
  "overall_safe": true/false,
  "recommendations": ["改进建议"]
}
"""


@dataclass
class VerifyResult:
    """Structured verification result."""
    intent_match: bool = True
    has_side_effects: bool = False
    side_effect_details: list[str] = field(default_factory=list)
    boundary_issues: list[str] = field(default_factory=list)
    quality_issues: list[str] = field(default_factory=list)
    overall_safe: bool = True
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent_match": self.intent_match,
            "has_side_effects": self.has_side_effects,
            "side_effect_details": self.side_effect_details,
            "boundary_issues": self.boundary_issues,
            "quality_issues": self.quality_issues,
            "overall_safe": self.overall_safe,
            "recommendations": self.recommendations,
        }


class VerifyAgent:
    """Semantic verification agent.

    Goes beyond syntax checking (compileall) to verify:
    - The change matches user intent
    - No unexpected side effects on callers
    - Edge cases are handled
    - Code quality is maintained
    """

    def __init__(self) -> None:
        self.llm = LLMClient()

    def verify(
        self,
        question: str,
        old_code: str,
        new_code: str,
        patch_summary: str,
        affected_files: list[str],
        test_output: str | None = None,
    ) -> VerifyResult:
        """Semantically verify a code change.

        Args:
            question: Original user request.
            old_code: Code before the change.
            new_code: Code after the change.
            patch_summary: Summary of what changed.
            affected_files: Files that might be affected.
            test_output: Pytest/compileall output if available.
        """
        # If LLM is unavailable, do basic heuristic verification
        if not self.llm.enabled:
            return self._heuristic_verify(old_code, new_code, test_output)

        return self._llm_verify(
            question, old_code, new_code, patch_summary, affected_files, test_output
        )

    def _heuristic_verify(
        self, old_code: str, new_code: str, test_output: str | None
    ) -> VerifyResult:
        """Basic heuristic verification without LLM."""
        issues: list[str] = []
        boundary_issues: list[str] = []

        # Check: new code must be syntactically valid Python
        try:
            import ast
            ast.parse(new_code)
        except SyntaxError as exc:
            issues.append(f"语法错误: {exc}")

        # Check: new code must not be empty
        if not new_code.strip():
            issues.append("修改后代码为空")

        # Check test results
        if test_output and "FAILED" in test_output:
            issues.append(f"测试失败: {test_output[:200]}")

        # Check for obvious missing validation
        if "password" in new_code.lower() and "password" in old_code.lower():
            if old_code.count("if not") < new_code.count("if not"):
                pass  # added validation — good
            elif "if not username" not in new_code and "password" in new_code:
                if "校验" not in old_code and "if not" not in new_code:
                    boundary_issues.append("密码字段可能需要空值校验")

        return VerifyResult(
            intent_match=len(issues) == 0,
            has_side_effects=False,
            quality_issues=issues,
            boundary_issues=boundary_issues,
            overall_safe=len(issues) == 0,
            recommendations=(
                ["修复语法错误"] if issues else []
            ),
        )

    def _llm_verify(
        self,
        question: str,
        old_code: str,
        new_code: str,
        patch_summary: str,
        affected_files: list[str],
        test_output: str | None,
    ) -> VerifyResult:
        """LLM-driven semantic verification."""
        # Truncate code for LLM context
        old_preview = old_code[:3000] + ("\n# ..." if len(old_code) > 3000 else "")
        new_preview = new_code[:3000] + ("\n# ..." if len(new_code) > 3000 else "")

        user_prompt_parts = [
            f"## 用户请求\n{question}",
            f"## 修改摘要\n{patch_summary}",
            f"## 可能受影响的文件\n{chr(10).join(f'- {f}' for f in affected_files) if affected_files else '(未知)'}",
            f"## 修改前代码\n```python\n{old_preview}\n```",
            f"## 修改后代码\n```python\n{new_preview}\n```",
        ]
        if test_output:
            user_prompt_parts.append(f"## 测试输出\n```\n{test_output[:500]}\n```")

        result = self.llm.chat_json(
            VERIFY_SYSTEM_PROMPT, "\n".join(user_prompt_parts), timeout=30.0
        )

        if not result:
            return self._heuristic_verify(old_code, new_code, test_output)

        return VerifyResult(
            intent_match=bool(result.get("intent_match", True)),
            has_side_effects=bool(result.get("has_side_effects", False)),
            side_effect_details=self._as_str_list(result.get("side_effect_details", [])),
            boundary_issues=self._as_str_list(result.get("boundary_issues", [])),
            quality_issues=self._as_str_list(result.get("quality_issues", [])),
            overall_safe=bool(result.get("overall_safe", True)),
            recommendations=self._as_str_list(result.get("recommendations", [])),
        )

    def _as_str_list(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if str(item).strip()]
