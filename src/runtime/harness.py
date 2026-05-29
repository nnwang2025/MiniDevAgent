from __future__ import annotations

from pathlib import Path
from typing import Any

from ..agents.answer_agent import AnswerAgent
from ..agents.planner_agent import PlannerAgent
from ..graph import AgentGraph
from ..memory.code_index import CodeIndex
from ..memory.cold_memory import ColdMemory
from ..memory.hot_memory import HotMemory
from ..skills.registry import SkillRouter
from ..tools.file_tools import build_project_index, normalize_project_path
from ..utils.logger import get_logger
from .context_manager import ContextManager
from .patch_generator import PatchManager
from .state import SessionState


class HarnessRuntime:
    def __init__(self) -> None:
        self.logger = get_logger(__name__)
        self.sessions: dict[str, SessionState] = {}
        self.planner = PlannerAgent()
        self.skill_router = SkillRouter()
        self.answer_agent = AnswerAgent()
        self.graph = AgentGraph()

    def create_session(self, project_path: str, session_id: str) -> dict[str, Any]:
        root = normalize_project_path(project_path)
        path_obj = Path(root)
        if not path_obj.exists() or not path_obj.is_dir():
            raise FileNotFoundError(f"项目路径不存在：{project_path}")
        project_index = build_project_index(root)
        state = SessionState(
            project_path=root,
            session_id=session_id,
            hot_memory=HotMemory(),
            cold_memory=ColdMemory.from_project(root),
            code_index=CodeIndex(root=root, files=project_index["files"]),
        )
        self.sessions[session_id] = state
        self.logger.info("Created session %s for project %s", session_id, root)
        return {"session_id": session_id, "project_path": root, "project_index": project_index}

    def ask(self, session_id: str, question: str) -> dict[str, Any]:
        if session_id not in self.sessions:
            raise KeyError(f"会话不存在：{session_id}。请先调用 /session/create 创建会话。")

        state = self.sessions[session_id]
        state.agent_trace = []
        state.hot_memory.add_message(question)

        plan = self.planner.plan(question)
        state.record_trace("规划", {"task_type": plan["task_type"], "reason": plan["reason"]})
        state.record_trace("技能选择", {"skill": self.skill_router.skill_name(plan["task_type"])})

        skill_result = self.skill_router.dispatch(question, plan, state)
        state.record_trace("工具调用", {"skill": skill_result.get("skill"), "tools": skill_result.get("tools_used", []), "files_used": skill_result.get("files_used", [])})
        state.record_trace("观察结果", skill_result.get("trace_observation", {}))

        if skill_result.get("pending_patch"):
            state.metadata["pending_patch"] = skill_result["pending_patch"]
            state.record_trace("Patch生成", {"file": skill_result["pending_patch"].get("file"), "summary": skill_result["pending_patch"].get("summary"), "dry_run": True})

        for file_path in skill_result.get("files_used", []):
            state.hot_memory.add_file(file_path)

        context_manager = ContextManager(state.hot_memory, state.cold_memory)
        compressed = context_manager.compress(plan["task_type"], skill_result, question)
        state.record_trace("上下文压缩", compressed["trace"])

        final_answer = self.answer_agent.answer(question, {"plan": plan, "compressed_context": compressed["context"]}, skill_result)
        state.record_trace("最终回答", {"files_used": final_answer["files_used"], "skill": skill_result.get("skill")})
        return {
            "planner_type": plan.get("task_type", ""),
            "skill": skill_result.get("skill", ""),
            "answer": final_answer["answer"],
            "related_records": final_answer.get("related_records", []),
            "files_used": final_answer["files_used"],
            "evidence": final_answer["evidence"],
            "agent_trace": state.agent_trace,
            "pending_patch": skill_result.get("pending_patch"),
        }

    def apply_pending_patch(self, session_id: str) -> dict[str, Any]:
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
        return {"applied": True, "apply": apply_result, "verification": verify_result}
