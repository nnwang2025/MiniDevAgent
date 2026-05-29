from __future__ import annotations

from pathlib import Path
import shutil
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.runtime.harness import HarnessRuntime


def copy_project() -> Path:
    temp_dir = ROOT / ".tmp_maintenance_tests" / uuid.uuid4().hex
    temp_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "demo_project", temp_dir / "demo_project", ignore=shutil.ignore_patterns("__pycache__"))
    return temp_dir / "demo_project"


def main() -> None:
    project = copy_project()
    harness = HarnessRuntime()
    harness.create_session(str(project), "maint")

    auth_path = project / "auth.py"
    original_auth = auth_path.read_text(encoding="utf-8")
    result = harness.ask("maint", "给 login() 增加空用户名和空密码校验")
    assert result["planner_type"] == "code_edit", result["planner_type"]
    assert result["pending_patch"], result
    assert "+        if not username or not password:" in result["pending_patch"]["diff"]
    assert auth_path.read_text(encoding="utf-8") == original_auth, "dry-run should not write"

    applied = harness.apply_pending_patch("maint")
    assert applied["applied"] is True, applied
    assert (project / "auth.py.bak").exists(), "backup missing"
    assert "if not username or not password" in auth_path.read_text(encoding="utf-8")
    assert "return_code" in applied["verification"]["compileall"]["data"], applied["verification"]
    assert applied["verification"]["success"] is True, applied["verification"]

    fix_project = copy_project()
    fix_harness = HarnessRuntime()
    fix_harness.create_session(str(fix_project), "fix")
    traceback_question = """修复这个示例 traceback:
Traceback (most recent call last):
  File "main.py", line 1, in <module>
    AuthService({}).login("", None)
  File "auth.py", line 10, in login
    return bool(user and user.get("password") == password)
TypeError: empty password should be rejected
"""
    fix = fix_harness.ask("fix", traceback_question)
    assert fix["planner_type"] == "code_fix", fix["planner_type"]
    assert fix["pending_patch"], fix

    opt_project = copy_project()
    opt_harness = HarnessRuntime()
    opt_harness.create_session(str(opt_project), "opt")
    original_main = (opt_project / "main.py").read_text(encoding="utf-8")
    opt = opt_harness.ask("opt", "优化 bootstrap()")
    assert opt["planner_type"] == "code_edit", opt["planner_type"]
    assert opt["pending_patch"], opt
    assert "user_store" in opt["pending_patch"]["diff"], opt["pending_patch"]["diff"]
    assert (opt_project / "main.py").read_text(encoding="utf-8") == original_main, "optimization should stay dry-run"

    print("maintenance smoke tests passed")


if __name__ == "__main__":
    main()
