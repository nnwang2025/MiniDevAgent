from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import difflib
import ast
import json
from pathlib import Path
from typing import Any

from ..mcp.registry import create_mcp_client


@dataclass
class PatchCandidate:
    file: str
    summary: str
    old_code: str
    new_code: str
    diff: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "summary": self.summary,
            "old_code": self.old_code,
            "new_code": self.new_code,
            "diff": self.diff,
        }


class PatchGenerator:
    def generate(self, file_path: str, old_content: str, new_content: str, summary: str) -> dict[str, Any]:
        diff = "".join(
            difflib.unified_diff(
                old_content.splitlines(keepends=True),
                new_content.splitlines(keepends=True),
                fromfile=f"a/{file_path}",
                tofile=f"b/{file_path}",
            )
        )
        return PatchCandidate(file_path, summary, old_content, new_content, diff).to_dict()


class PatchManager:
    def __init__(self, project_root: str) -> None:
        self.project_root = Path(project_root).resolve()
        self.mcp = create_mcp_client(str(self.project_root))

    def apply_patch(self, patch: dict[str, Any]) -> dict[str, Any]:
        file_path = patch["file"]
        target = (self.project_root / file_path).resolve()
        if self.project_root != target and self.project_root not in target.parents:
            raise ValueError("Patch target outside project root.")
        if not target.is_file():
            raise FileNotFoundError(file_path)
        current = target.read_text(encoding="utf-8")
        if current != patch["old_code"]:
            raise ValueError("File content changed after patch generation; refusing to apply stale patch.")
        backup = target.with_name(target.name + ".bak")
        backup.write_text(current, encoding="utf-8")
        target.write_text(patch["new_code"], encoding="utf-8")
        self._record_history(patch, str(backup.relative_to(self.project_root)))
        return {"applied": True, "file": file_path, "backup": str(backup.relative_to(self.project_root))}

    def verify(self) -> dict[str, Any]:
        compile_result = self.mcp.call("terminal", "run_python", args=["-m", "compileall", "-q", "."], timeout=30)
        syntax_fallback = None
        compile_ok = compile_result.get("ok") and compile_result.get("data", {}).get("return_code") == 0
        compile_log = (compile_result.get("data", {}).get("stdout", "") + compile_result.get("data", {}).get("stderr", ""))
        if not compile_ok and "PermissionError" in compile_log:
            syntax_fallback = self._syntax_check()
        pytest_exists = (self.project_root / "pytest.ini").exists() or (self.project_root / "tests").exists()
        pytest_result = None
        if pytest_exists:
            pytest_result = self.mcp.call("terminal", "run_pytest", args=[], timeout=30)
        success = compile_ok or bool(syntax_fallback and syntax_fallback.get("success"))
        if pytest_result is not None:
            success = success and pytest_result.get("ok") and pytest_result.get("data", {}).get("return_code") == 0
        return {"success": bool(success), "compileall": compile_result, "syntax_fallback": syntax_fallback, "pytest": pytest_result}

    def _syntax_check(self) -> dict[str, Any]:
        checked = []
        errors = []
        for path in self.project_root.rglob("*.py"):
            if any(part in {".venv", ".git", "__pycache__"} for part in path.parts):
                continue
            try:
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                checked.append(str(path.relative_to(self.project_root)))
            except Exception as exc:
                errors.append({"path": str(path.relative_to(self.project_root)), "error": str(exc)})
        return {"success": not errors, "checked": checked, "errors": errors, "reason": "compileall could not write pyc in this sandbox; AST syntax check used as fallback."}

    def _record_history(self, patch: dict[str, Any], backup: str) -> None:
        history_dir = self.project_root / ".minidevagent"
        history_dir.mkdir(exist_ok=True)
        record = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "file": patch["file"],
            "summary": patch.get("summary", ""),
            "backup": backup,
            "diff": patch.get("diff", ""),
        }
        with (history_dir / "patch_history.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
