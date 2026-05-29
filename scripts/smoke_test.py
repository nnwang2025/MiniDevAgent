from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from src.api.app import create_app
from src.runtime.harness import HarnessRuntime


QUESTIONS = [
    ("这个项目是什么", "project_overview"),
    ("项目整体结构", "project_overview"),
    ("代码执行入口在哪里", "entrypoint_analysis"),
    ("login() 干嘛", "function_analysis"),
    ("AuthService 被谁调用", "dependency_trace"),
    ("数据库在哪里初始化", "dependency_trace"),
    ("之前为什么改项目方向", "memory_recall"),
    ("那个 bug 怎么解决的", "problem_trace"),
]


def main() -> None:
    harness = HarnessRuntime()
    harness.create_session(str(ROOT), "smoke")
    results = []
    for question, expected_type in QUESTIONS:
        result = harness.ask("smoke", question)
        assert result["planner_type"] == expected_type, (question, result["planner_type"], expected_type)
        assert result["planner_type"] != "general_question", question
        assert result["skill"], question
        assert result["agent_trace"], question
        results.append((question, result))

    login_result = dict(results)["login() 干嘛"]
    assert login_result["files_used"] == ["demo_project\\auth.py"] or login_result["files_used"] == ["demo_project/auth.py"], login_result["files_used"]

    dep_result = dict(results)["AuthService 被谁调用"]
    assert all("prompts" not in path.replace("\\", "/") and "skills" not in path.replace("\\", "/") for path in dep_result["files_used"]), dep_result["files_used"]

    memory_result = dict(results)["之前为什么改项目方向"]
    assert ".minidevagent" in "\n".join(memory_result["evidence"]), memory_result["evidence"]
    assert "hot_memory_used: False" in str(memory_result["agent_trace"]) or "'hot_memory_used': False" in str(memory_result["agent_trace"])

    problem_result = dict(results)["那个 bug 怎么解决的"]
    assert "未找到" in problem_result["answer"], problem_result["answer"]

    app = create_app()
    client = TestClient(app)
    session = client.post("/session/create", json={"project_path": str(ROOT), "session_id": "api-smoke"})
    assert session.status_code == 200, session.text
    ask = client.post("/ask", json={"session_id": "api-smoke", "question": "代码执行入口在哪里", "trace": True})
    assert ask.status_code == 200, ask.text
    payload = ask.json()
    assert payload["planner_type"] == "entrypoint_analysis", payload
    assert payload["agent_trace"], payload

    print("smoke tests passed")


if __name__ == "__main__":
    main()
