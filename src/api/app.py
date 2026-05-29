from __future__ import annotations

from fastapi import FastAPI

from ..runtime.harness import HarnessRuntime
from .routes import router


def create_app() -> FastAPI:
    app = FastAPI(
        title="MiniDevAgent",
        version="0.3.0",
        description="MiniDevAgent 本地代码工程助手。运行链路：PlannerAgent -> SkillRouter -> Skill -> ContextManager -> AnswerAgent。",
    )
    app.state.harness = HarnessRuntime()
    app.include_router(router)
    return app


app = create_app()
