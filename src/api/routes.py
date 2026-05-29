from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from .schemas import AskRequest, AskResponse, ProjectIndexResponse, SessionCreateRequest

router = APIRouter()


@router.get("/", response_class=HTMLResponse, summary="中文调试页面")
def home() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>MiniDevAgent</title></head>
<body>
  <main>
    <h1>MiniDevAgent Skill Runtime</h1>
    <p>先调用 /session/create 创建会话，再向 /ask 提问。Swagger 文档在 <a href="/docs">/docs</a>。</p>
  </main>
</body>
</html>"""


@router.get("/health", summary="健康检查")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "MiniDevAgent"}


@router.post("/session/create", summary="创建代码分析会话")
def create_session(request: Request, payload: SessionCreateRequest) -> dict[str, str]:
    harness = request.app.state.harness
    try:
        session = harness.create_session(payload.project_path, payload.session_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"session_id": session["session_id"], "project_path": session["project_path"]}


@router.post("/ask", response_model=AskResponse, summary="向技能运行时提问")
def ask_question(request: Request, payload: AskRequest) -> AskResponse:
    harness = request.app.state.harness
    try:
        result = harness.ask(payload.session_id, payload.question)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not payload.trace:
        result["agent_trace"] = result.get("agent_trace", [])
    return AskResponse(**result)


@router.get("/project/index", response_model=ProjectIndexResponse, summary="生成项目索引")
def project_index(request: Request, project_path: str) -> ProjectIndexResponse:
    harness = request.app.state.harness
    try:
        session = harness.create_session(project_path, session_id="index-only")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    index = session["project_index"]
    return ProjectIndexResponse(project_path=index["root"], count=index["count"], files=index["files"])
