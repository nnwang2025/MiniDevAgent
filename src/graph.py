from __future__ import annotations

import logging
from typing import Any, Literal

logger = logging.getLogger(__name__)

try:
    from langgraph.graph import StateGraph, END
    from typing import TypedDict

    LANGGRAPH_AVAILABLE = True
except ImportError:  # pragma: no cover
    StateGraph = None  # type: ignore
    TypedDict = dict  # type: ignore
    END = "END"
    LANGGRAPH_AVAILABLE = False


# ── Agent State Schema ────────────────────────────────────────────

if LANGGRAPH_AVAILABLE:

    class AgentState(TypedDict, total=False):
        """State that flows through the PERR graph nodes."""
        question: str
        session_id: str
        project_path: str

        # Planning
        plan: dict[str, Any]
        plan_confidence: float

        # Execution
        skill_result: dict[str, Any]
        execution_round: int

        # Verification
        verification_result: dict[str, Any] | None

        # Reflection
        reflection: dict[str, Any]
        should_refine: bool
        refine_round: int

        # Final
        final_response: dict[str, Any]

else:
    AgentState = dict  # type: ignore


# ── Default node functions (overridden at runtime) ───────────────

# These are placeholder implementations; the real logic is injected
# via method binding when the AgentGraph is connected to HarnessRuntime.

_PLANNER = None
_SKILL_ROUTER = None
_REFLECTION_AGENT = None
_ANSWER_AGENT = None
_PATCH_MANAGER = None
_MAX_REFINE_ROUNDS = 3


def _plan_node(state: AgentState) -> dict[str, Any]:
    """Plan node: classify the question and decompose into subtasks."""
    if _PLANNER is None:
        return {"plan": {"task_type": "project_overview", "reason": "Planner not configured."}}
    plan = _PLANNER.plan(state["question"])
    logger.info("Graph PLAN: task_type=%s, confidence=%.2f", plan.get("task_type"), plan.get("confidence", 0))
    return {
        "plan": plan,
        "plan_confidence": plan.get("confidence", 0.5),
        "refine_round": 0,
    }


def _execute_node(state: AgentState) -> dict[str, Any]:
    """Execute node: dispatch to the appropriate skill."""
    if _SKILL_ROUTER is None:
        return {"skill_result": {"answer": "SkillRouter not configured.", "files_used": [], "evidence": []}}
    plan = state.get("plan", {})
    # We need a minimal runtime_state proxy for skill binding
    skill_result = _SKILL_ROUTER.dispatch(
        state["question"],
        plan,
        _RuntimeStateProxy(state.get("project_path", ".")),
    )
    logger.info("Graph EXECUTE: skill=%s, files=%d", skill_result.get("skill"), len(skill_result.get("files_used", [])))
    return {
        "skill_result": skill_result,
        "execution_round": state.get("execution_round", 0) + 1,
    }


def _verify_node(state: AgentState) -> dict[str, Any]:
    """Verify node: run compileall + tests for code tasks."""
    skill_result = state.get("skill_result", {})
    task_type = skill_result.get("planner_type", state.get("plan", {}).get("task_type", ""))
    if task_type not in {"code_edit", "code_fix"}:
        return {"verification_result": None}
    patch = skill_result.get("pending_patch")
    if not patch or _PATCH_MANAGER is None:
        return {"verification_result": {"success": False, "message": "无 patch 或 PatchManager 未配置"}}
    verify_result = _PATCH_MANAGER.verify()
    logger.info("Graph VERIFY: success=%s", verify_result.get("success"))
    return {"verification_result": verify_result}


def _reflect_node(state: AgentState) -> dict[str, Any]:
    """Reflect node: structured evaluation of execution results."""
    if _REFLECTION_AGENT is None:
        return {"reflection": {"success": True, "confidence": 0.9}, "should_refine": False}
    plan = state.get("plan", {})
    skill_result = state.get("skill_result", {})
    verification = state.get("verification_result")
    refine_round = state.get("refine_round", 0)
    reflection = _REFLECTION_AGENT.reflect(
        question=state["question"],
        plan=plan,
        execution_result=skill_result,
        verification_result=verification,
        retry_count=refine_round,
        max_retries=_MAX_REFINE_ROUNDS,
    )
    logger.info(
        "Graph REFLECT: success=%s, confidence=%.2f, should_retry=%s",
        reflection.success, reflection.confidence, reflection.should_retry,
    )
    return {
        "reflection": reflection.to_dict(),
        "should_refine": reflection.should_retry,
    }


def _refine_node(state: AgentState) -> dict[str, Any]:
    """Refine node: update the plan based on reflection feedback."""
    plan = state.get("plan", {})
    reflection_data = state.get("reflection", {})
    refine_round = state.get("refine_round", 0) + 1
    # Update plan with refinement feedback
    refined_plan = dict(plan)
    refinement_steps = reflection_data.get("refinement_plan", [])
    if refinement_steps:
        refined_plan["sub_tasks"] = refinement_steps
    refined_plan["confidence"] = reflection_data.get("confidence", 0.5)
    issues = reflection_data.get("issues_found", [])
    if issues:
        refined_plan["reason"] = f"{plan.get('reason', '')} [修正轮{refine_round}: {'; '.join(issues[:3])}]"
    refined_plan["planner"] = "refine"
    logger.info("Graph REFINE: round=%d, issues=%d", refine_round, len(issues))
    return {"plan": refined_plan, "refine_round": refine_round}


def _finalize_node(state: AgentState) -> dict[str, Any]:
    """Finalize node: compress context and generate answer."""
    if _ANSWER_AGENT is None:
        return {"final_response": {"answer": "AnswerAgent not configured."}}
    plan = state.get("plan", {})
    skill_result = state.get("skill_result", {})
    final_answer = _ANSWER_AGENT.answer(
        state["question"],
        {"plan": plan, "compressed_context": {}},
        skill_result,
    )
    return {
        "final_response": {
            "planner_type": plan.get("task_type", ""),
            "skill": skill_result.get("skill", ""),
            "answer": final_answer["answer"],
            "files_used": final_answer["files_used"],
            "evidence": final_answer["evidence"],
            "related_records": final_answer.get("related_records", []),
            "pending_patch": skill_result.get("pending_patch"),
            "perr_rounds": state.get("refine_round", 0) + 1,
        }
    }


def _should_continue(state: AgentState) -> Literal["refine", "finalize"]:
    """Conditional edge: should we refine or finalize?"""
    if state.get("should_refine") and state.get("refine_round", 0) < _MAX_REFINE_ROUNDS:
        return "refine"
    return "finalize"


def _needs_verification(state: AgentState) -> Literal["verify", "reflect"]:
    """Conditional: does this task type need verification?"""
    plan = state.get("plan", {})
    if plan.get("task_type") in {"code_edit", "code_fix"}:
        return "verify"
    return "reflect"


class _RuntimeStateProxy:
    """Minimal state proxy for skill binding."""
    def __init__(self, project_path: str) -> None:
        self.project_path = project_path


# ── AgentGraph ────────────────────────────────────────────────────

class AgentGraph:
    """LangGraph-based PERR (Plan-Execute-Reflect-Refine) state machine.

    Graph structure:
        PLAN → EXECUTE → [needs_verify?]
                           ├─ yes → VERIFY → REFLECT
                           └─ no  → REFLECT
        REFLECT → [should_continue?]
                   ├─ refine → REFINE → EXECUTE
                   └─ finalize → FINALIZE → END

    This makes the agent's decision flow explicit and visualizable,
    which is a major differentiator from implicit ReAct loops.
    """

    def __init__(self) -> None:
        self.graph: Any = None
        if LANGGRAPH_AVAILABLE and StateGraph is not None:
            try:
                self.graph = self._build_graph()
                logger.info("LangGraph PERR state machine initialized.")
            except Exception as exc:
                logger.warning("LangGraph graph build failed: %s", exc)
                self.graph = None
        else:
            logger.info("LangGraph not available; using sequential fallback in HarnessRuntime.")

    def _build_graph(self) -> Any:
        """Build the PERR state graph."""
        builder = StateGraph(AgentState)

        # Add nodes
        builder.add_node("plan", _plan_node)
        builder.add_node("execute", _execute_node)
        builder.add_node("verify", _verify_node)
        builder.add_node("reflect", _reflect_node)
        builder.add_node("refine", _refine_node)
        builder.add_node("finalize", _finalize_node)

        # Set entry
        builder.set_entry_point("plan")

        # Plan → Execute
        builder.add_edge("plan", "execute")

        # Execute → Verify or Reflect
        builder.add_conditional_edges(
            "execute",
            _needs_verification,
            {"verify": "verify", "reflect": "reflect"},
        )

        # Verify → Reflect
        builder.add_edge("verify", "reflect")

        # Reflect → Refine or Finalize
        builder.add_conditional_edges(
            "reflect",
            _should_continue,
            {"refine": "refine", "finalize": "finalize"},
        )

        # Refine → Execute (back to loop)
        builder.add_edge("refine", "execute")

        # Finalize → END
        builder.add_edge("finalize", END)

        return builder.compile()

    def bind_runtime(
        self,
        planner: Any,
        skill_router: Any,
        reflection_agent: Any,
        answer_agent: Any,
        patch_manager_factory: Any = None,
        max_refine_rounds: int = 3,
    ) -> None:
        """Bind runtime components to the graph nodes.

        Call this after creating HarnessRuntime to connect the real
        agent instances to the LangGraph nodes.
        """
        global _PLANNER, _SKILL_ROUTER, _REFLECTION_AGENT, _ANSWER_AGENT
        global _PATCH_MANAGER, _MAX_REFINE_ROUNDS
        _PLANNER = planner
        _SKILL_ROUTER = skill_router
        _REFLECTION_AGENT = reflection_agent
        _ANSWER_AGENT = answer_agent
        _PATCH_MANAGER = patch_manager_factory
        _MAX_REFINE_ROUNDS = max_refine_rounds
        logger.info("AgentGraph bound to runtime components.")

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Run the graph on the given inputs.

        If LangGraph is unavailable, returns inputs unchanged
        (fallback to HarnessRuntime's internal loop).
        """
        if self.graph is None:
            logger.info("Running fallback sequential agent flow (LangGraph unavailable).")
            return inputs

        logger.info("Running LangGraph PERR flow.")
        try:
            result = self.graph.invoke(inputs)
            return result.get("final_response", inputs)
        except Exception as exc:
            logger.warning("LangGraph invocation failed, falling back: %s", exc)
            return inputs
