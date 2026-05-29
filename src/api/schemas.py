from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SessionCreateRequest(BaseModel):
    project_path: str = Field(..., description="目标项目路径，例如 demo_project 或 .")
    session_id: str = Field(..., description="会话 ID，用于后续提问复用记忆。")


class AskRequest(BaseModel):
    session_id: str = Field(..., description="已创建的会话 ID")
    question: str = Field(..., description="要询问的代码工程问题")
    trace: bool = Field(False, description="是否需要 agent_trace。响应模型保留该字段。")


class AskResponse(BaseModel):
    planner_type: str = Field(..., description="Planner 识别出的任务类型")
    skill: str = Field(..., description="实际执行的技能名称")
    answer: str = Field(..., description="统一中文回答")
    related_records: list[str] = Field(default_factory=list, description="记忆或问题追踪关联记录")
    files_used: list[str] = Field(default_factory=list, description="回答涉及的文件")
    evidence: list[str] = Field(default_factory=list, description="代码或记忆证据")
    agent_trace: list[dict[str, Any]] = Field(default_factory=list, description="运行时执行轨迹")
    pending_patch: dict[str, Any] | None = Field(default=None, description="dry-run patch；API 不会自动应用。")


class ProjectIndexResponse(BaseModel):
    project_path: str = Field(..., description="项目绝对路径")
    count: int = Field(..., description="索引到的 Python 文件数量")
    files: list[dict[str, str]] = Field(default_factory=list, description="文件摘要列表")
