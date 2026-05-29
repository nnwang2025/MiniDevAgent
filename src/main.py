from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from src.runtime.harness import HarnessRuntime
    from src.utils.logger import get_logger
except ImportError:
    from runtime.harness import HarnessRuntime
    from utils.logger import get_logger

logger = get_logger(__name__)


def run_cli(project_path: str, session_id: str = "cli", trace: bool = False, questions: list[str] | None = None, auto_yes: bool = False) -> None:
    harness = HarnessRuntime()
    root = Path(project_path).resolve()
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(f"项目路径不存在：{project_path}")
    harness.create_session(str(root), session_id)
    print(f"MiniDevAgent CLI 已就绪，当前分析项目：{root}")
    if questions:
        for question in questions:
            _ask_and_maybe_apply(harness, session_id, question, trace=trace, auto_yes=auto_yes, interactive=False)
        return
    print("请输入代码库问题，或输入 'exit' 退出。")
    while True:
        try:
            question = input("问题> ").strip()
        except KeyboardInterrupt:
            print("\n已退出。")
            break
        if not question or question.lower() in {"exit", "quit"}:
            break
        _ask_and_maybe_apply(harness, session_id, question, trace=trace, auto_yes=auto_yes, interactive=True)


def _ask_and_maybe_apply(harness: HarnessRuntime, session_id: str, question: str, trace: bool, auto_yes: bool, interactive: bool) -> None:
    response = harness.ask(session_id, question)
    print(_format_trace(response) if trace else f"\n{response['answer']}\n")
    if not response.get("pending_patch"):
        return
    confirm = "yes" if auto_yes else ""
    if interactive and not auto_yes:
        confirm = input("是否应用该修改？输入 yes 确认：").strip().lower()
    if confirm == "yes":
        result = harness.apply_pending_patch(session_id)
        print(_format_verification(result))
    else:
        print("保持 dry-run：未写入文件。")


def _format_trace(response: dict[str, Any]) -> str:
    lines = ["", "【Agent 执行轨迹】"]
    for item in response.get("agent_trace", []):
        step = item.get("step", "")
        lines.append(f"【{step}】")
        lines.append(_compact_detail(item.get("detail", {})))
    lines.append("【最终回答正文】")
    lines.append(response["answer"])
    lines.append("")
    return "\n".join(lines)


def _format_verification(result: dict[str, Any]) -> str:
    lines = ["【执行验证】", f"- applied: {result.get('applied')}"]
    apply_result = result.get("apply", {})
    if apply_result:
        lines.append(f"- file: {apply_result.get('file')}")
        lines.append(f"- backup: {apply_result.get('backup')}")
    verification = result.get("verification", {})
    if verification:
        lines.append(f"- success: {verification.get('success')}")
        compile_data = verification.get("compileall", {}).get("data", {})
        lines.append(f"- compileall return_code: {compile_data.get('return_code')}")
        if compile_data.get("stderr"):
            lines.append(f"- compileall stderr: {compile_data.get('stderr')[:500]}")
        pytest = verification.get("pytest")
        if pytest:
            lines.append(f"- pytest return_code: {pytest.get('data', {}).get('return_code')}")
    return "\n".join(lines)


def _compact_detail(detail: Any) -> str:
    if isinstance(detail, dict):
        parts = []
        for key, value in detail.items():
            text = str(value)
            if len(text) > 500:
                text = text[:497] + "..."
            parts.append(f"- {key}: {text}")
        return "\n".join(parts) if parts else "- 无"
    return str(detail)


def main() -> None:
    parser = argparse.ArgumentParser(description="在本地项目上运行 MiniDevAgent。")
    parser.add_argument("project_path", help="目标项目路径。")
    parser.add_argument("--session-id", default="cli", help="会话标识。")
    parser.add_argument("--serve", action="store_true", help="运行 FastAPI 服务，而不是 CLI。")
    parser.add_argument("--port", type=int, default=8000, help="FastAPI 服务端口。")
    parser.add_argument("--trace", action="store_true", help="在 CLI 中输出完整 agent_trace。")
    parser.add_argument("--question", action="append", default=[], help="非交互提问；可重复传入。")
    parser.add_argument("--yes", action="store_true", help="非交互模式下确认应用 pending patch。")
    args = parser.parse_args()

    if args.serve:
        try:
            import uvicorn
        except ImportError as exc:
            raise RuntimeError("运行服务需要安装 uvicorn") from exc
        docs_url = f"http://127.0.0.1:{args.port}/docs"
        print(f"MiniDevAgent API 已启动：{docs_url}")
        uvicorn.run("src.api.app:app", host="127.0.0.1", port=args.port, reload=False)
    else:
        run_cli(args.project_path, args.session_id, trace=args.trace, questions=args.question, auto_yes=args.yes)


if __name__ == "__main__":
    main()
