from __future__ import annotations

from pathlib import Path
from typing import Any

from ..agents.answer_agent import AnswerAgent
from ..agents.planner_agent import PlannerAgent
from ..agents.reflection_agent import ReflectionAgent
from ..graph import AgentGraph
from ..ingestion.pipeline import IngestionPipeline
from ..memory.code_index import CodeIndex
from ..memory.cold_memory import ColdMemory
from ..memory.hot_memory import HotMemory
from ..memory.redis_backend import RedisMemoryBackend
from ..skills.registry import SkillRouter
from ..tools.file_tools import build_project_index, normalize_project_path
from ..utils.logger import get_logger
from .context_manager import ContextManager
from .patch_generator import PatchManager
from .state import SessionState


# Tasks where PERR loop adds value vs single-pass tasks.
PERR_TASK_TYPES = {"code_edit", "code_fix", "dependency_trace", "problem_trace"}
MAX_REFINE_ROUNDS = 3


class HarnessRuntime:
    """Master orchestrator implementing the PERR paradigm.

    Plan → Execute → Verify → Reflect → Refine (loop)
           ↑__________________________________|

    Simple tasks (project_overview, function_analysis, etc.) run single-pass.
    Complex tasks (code_edit, code_fix, dependency_trace) use the full PERR loop
    with structured reflection and up to MAX_REFINE_ROUNDS refinement rounds.
    """

    def __init__(self) -> None:
        self.logger = get_logger(__name__)
        self.sessions: dict[str, SessionState] = {}
        self.planner = PlannerAgent()
        self.skill_router = SkillRouter()
        self.answer_agent = AnswerAgent()
        self.reflection_agent = ReflectionAgent()
        self.graph = AgentGraph()

    # ── Session management ───────────────────────────────────────

    def create_session(self, project_path: str, session_id: str) -> dict[str, Any]:
        root = normalize_project_path(project_path)
        path_obj = Path(root)
        if not path_obj.exists() or not path_obj.is_dir():
            raise FileNotFoundError(f"项目路径不存在：{project_path}")
        project_index = build_project_index(root)

        # Build cold memory: load memory files + ingest multi-source data
        cold_memory = ColdMemory.from_project(root)
        ingestion = IngestionPipeline()
        ingested = ingestion.ingest_all(root)
        for record in ingested["records"]:
            cold_memory.records.append(record)

        # Rebuild chunks to include ingested records
        from ..memory.chunk_builder import build_episodic_chunks
        cold_memory.chunks = build_episodic_chunks(cold_memory.records)

        # Hot memory with optional Redis persistence
        hot_memory = HotMemory()
        redis_backend = RedisMemoryBackend.connect(session_id)
        if redis_backend.is_redis:
            hot_memory.bind_redis(redis_backend)

        state = SessionState(
            project_path=root,
            session_id=session_id,
            hot_memory=hot_memory,
            cold_memory=cold_memory,
            code_index=CodeIndex(root=root, files=project_index["files"]),
            metadata={"redis_backend": redis_backend},
        )
        self.sessions[session_id] = state
        self.logger.info(
            "Created session %s | memory: %d records (%s) | redis: %s",
            session_id, ingested["total_records"],
            ", ".join(f"{k}={v}" for k, v in ingested["by_source"].items()) or "none",
            redis_backend.health().get("backend", "unknown"),
        )
        return {
            "session_id": session_id,
            "project_path": root,
            "project_index": project_index,
            "ingestion_summary": ingested["by_source"],
            "redis": redis_backend.health(),
        }

    # ── Main entry point ─────────────────────────────────────────

    def ask(self, session_id: str, question: str) -> dict[str, Any]:
        """Process a user question through the agent pipeline.

        Uses PERR (Plan→Execute→Reflect→Refine) for complex tasks,
        single-pass pipeline for simple tasks.
        """
        if session_id not in self.sessions:
            raise KeyError(f"会话不存在：{session_id}。请先调用 /session/create 创建会话。")

        state = self.sessions[session_id]
        state.agent_trace = []
        state.hot_memory.add_message(question)

        # ── PHASE 1: PLAN ──
        plan = self.planner.plan(question)
        state.record_trace("规划", {
            "task_type": plan["task_type"],
            "complexity": plan.get("complexity", "simple"),
            "sub_tasks": plan.get("sub_tasks", []),
            "reason": plan["reason"],
            "planner": plan.get("planner", "rule"),
            "confidence": plan.get("confidence", 0.5),
        })

        use_perr = plan["task_type"] in PERR_TASK_TYPES and plan.get("complexity") in {"moderate", "complex"}

        if use_perr:
            result = self._run_perr_loop(question, plan, state)
        else:
            result = self._run_single_pass(question, plan, state)

        return result

    # ── PERR Loop (complex tasks) ────────────────────────────────

    def _run_perr_loop(
        self, question: str, plan: dict[str, Any], state: SessionState
    ) -> dict[str, Any]:
        """Full Plan→Execute→Verify→Reflect→Refine cycle."""
        self.logger.info("Starting PERR loop for task_type=%s", plan["task_type"])

        current_plan = plan
        all_reflections: list[dict[str, Any]] = []
        refinement_round = 0

        while refinement_round <= MAX_REFINE_ROUNDS:
            # ── EXECUTE ──
            skill_result = self._execute_skill(question, current_plan, state)
            state.record_trace("执行", {
                "round": refinement_round + 1,
                "tools": skill_result.get("tools_used", []),
                "files_used": skill_result.get("files_used", []),
            })
            state.record_trace("观察结果", skill_result.get("trace_observation", {}))

            # ── VERIFY ──
            verify_result = self._verify_execution(skill_result, state)
            state.record_trace("验证", verify_result if verify_result else {"note": "无额外验证步骤"})

            # ── REFLECT ──
            reflection = self.reflection_agent.reflect(
                question=question,
                plan=current_plan,
                execution_result=skill_result,
                verification_result=verify_result,
                retry_count=refinement_round,
                max_retries=MAX_REFINE_ROUNDS,
            )
            all_reflections.append(reflection.to_dict())
            state.record_trace("反思", reflection.to_dict())

            if reflection.success and reflection.confidence >= 0.85:
                self.logger.info("PERR: success after %d rounds", refinement_round + 1)
                break

            if not reflection.should_retry:
                self.logger.info(
                    "PERR: stopping at round %d (should_retry=False, confidence=%.2f)",
                    refinement_round + 1, reflection.confidence,
                )
                break

            # ── REFINE ──
            current_plan = self._refine_plan(current_plan, reflection)
            state.record_trace("计划修正", {
                "round": refinement_round + 1,
                "issues": reflection.issues_found,
                "refinement_plan": reflection.refinement_plan,
            })
            refinement_round += 1

        # ── FINALIZE ──
        # Re-execute with the latest plan to get the final skill_result
        state.record_trace("PERR完成", {
            "total_rounds": refinement_round + 1,
            "reflections": all_reflections,
        })

        return self._finalize_response(question, current_plan, skill_result, state)

    # ── Single-pass pipeline (simple tasks) ──────────────────────

    def _run_single_pass(
        self, question: str, plan: dict[str, Any], state: SessionState
    ) -> dict[str, Any]:
        """Single-pass pipeline for simple understanding/memory tasks."""
        skill_result = self._execute_skill(question, plan, state)
        state.record_trace("技能选择", {
            "skill": skill_result.get("skill"),
            "task_type": plan["task_type"],
        })
        state.record_trace("工具调用", {
            "tools": skill_result.get("tools_used", []),
            "files_used": skill_result.get("files_used", []),
        })
        state.record_trace("观察结果", skill_result.get("trace_observation", {}))

        return self._finalize_response(question, plan, skill_result, state)

    # ── Shared helpers ───────────────────────────────────────────

    def _execute_skill(
        self, question: str, plan: dict[str, Any], state: SessionState
    ) -> dict[str, Any]:
        """Execute the skill for the given plan. Returns skill_result dict."""
        skill_result = self.skill_router.dispatch(question, plan, state)

        # Handle pending patch
        if skill_result.get("pending_patch"):
            state.metadata["pending_patch"] = skill_result["pending_patch"]
            state.record_trace("Patch生成", {
                "file": skill_result["pending_patch"].get("file"),
                "summary": skill_result["pending_patch"].get("summary"),
                "dry_run": True,
            })

        # Track files in hot memory
        for file_path in skill_result.get("files_used", []):
            state.hot_memory.add_file(file_path)

        return skill_result

    def _verify_execution(
        self, skill_result: dict[str, Any], state: SessionState
    ) -> dict[str, Any] | None:
        """Run verification on execution results.

        For code tasks, runs syntax check + tests.
        For other tasks, returns None (no verification needed).
        """
        task_type = skill_result.get("planner_type", "")
        if task_type not in {"code_edit", "code_fix"}:
            return None

        patch = skill_result.get("pending_patch")
        if not patch:
            return {"success": False, "message": "未生成 patch，无法验证。"}

        manager = PatchManager(state.project_path)
        # Verify uses compileall + AST fallback + optional pytest
        return manager.verify()

    def _refine_plan(
        self, plan: dict[str, Any], reflection: "ReflectionResult"
    ) -> dict[str, Any]:
        """Refine the plan based on reflection feedback.

        This is the REFINE phase — we update the plan to address the
        issues found during reflection, without starting from scratch.
        """
        refined = dict(plan)  # shallow copy
        refined["planner"] = "refine"

        # Carry forward the refinement notes
        if reflection.refinement_plan:
            refined["sub_tasks"] = reflection.refinement_plan

        # Lower confidence — we're in a retry
        refined["confidence"] = reflection.confidence
        refined["reason"] = (
            f"{plan.get('reason', '')} [修正轮次: {reflection.issues_found}]"
        )

        return refined

    def _finalize_response(
        self,
        question: str,
        plan: dict[str, Any],
        skill_result: dict[str, Any],
        state: SessionState,
    ) -> dict[str, Any]:
        """Compress context, generate final answer, build response."""
        # ── Context compression ──
        context_manager = ContextManager(state.hot_memory, state.cold_memory)
        compressed = context_manager.compress(
            plan["task_type"], skill_result, question
        )
        state.record_trace("上下文压缩", compressed["trace"])

        # ── Generate answer ──
        final_answer = self.answer_agent.answer(
            question,
            {"plan": plan, "compressed_context": compressed["context"]},
            skill_result,
        )
        state.record_trace("最终回答", {
            "files_used": final_answer["files_used"],
            "skill": skill_result.get("skill"),
        })

        # ── Build response ──
        return {
            "planner_type": plan.get("task_type", ""),
            "skill": skill_result.get("skill", ""),
            "answer": final_answer["answer"],
            "related_records": final_answer.get("related_records", []),
            "files_used": final_answer["files_used"],
            "evidence": final_answer["evidence"],
            "agent_trace": state.agent_trace,
            "pending_patch": skill_result.get("pending_patch"),
            # PERR-specific metadata
            "perr_rounds": len(
                [t for t in state.agent_trace if t["step"] == "反思"]
            ),
        }

    # ── Patch application (human-in-the-loop) ────────────────────

    def apply_pending_patch(self, session_id: str) -> dict[str, Any]:
        """Apply a pending patch with safety checks and verification."""
        if session_id not in self.sessions:
            raise KeyError(f"会话不存在：{session_id}")
        state = self.sessions[session_id]
        patch = state.metadata.get("pending_patch")
        if not patch:
            return {"applied": False, "message": "没有待应用 patch。"}

        manager = PatchManager(state.project_path)
        apply_result = manager.apply_patch(patch)
        verify_result = manager.verify()
        state.metadata.pop("pending_patch", None)
        state.record_trace("执行验证", verify_result)

        # Write to cold memory for future recall
        self._record_patch_to_memory(state, patch, apply_result, verify_result)

        return {
            "applied": True,
            "apply": apply_result,
            "verification": verify_result,
        }

    def _record_patch_to_memory(
        self,
        state: SessionState,
        patch: dict[str, Any],
        apply_result: dict[str, Any],
        verify_result: dict[str, Any],
    ) -> None:
        """Record the applied patch in cold memory for future recall."""
        try:
            import json
            from datetime import datetime

            record = {
                "timestamp": datetime.now().isoformat(),
                "kind": "patch",
                "file": patch.get("file", ""),
                "summary": patch.get("summary", ""),
                "apply_success": apply_result.get("success", False),
                "verify_success": verify_result.get("success", False),
            }

            memory_path = Path(state.project_path) / ".minidevagent" / "memory_records.json"
            records = []
            if memory_path.exists():
                records = json.loads(memory_path.read_text(encoding="utf-8"))
                if not isinstance(records, list):
                    records = []
            records.append(record)
            memory_path.write_text(
                json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as exc:
            self.logger.debug("Failed to record patch to cold memory: %s", exc)
