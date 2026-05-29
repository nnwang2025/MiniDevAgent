from __future__ import annotations

import subprocess
import sys
import os
from typing import Any

from .base import MCPServer


class TerminalMCPServer(MCPServer):
    name = "terminal"

    def run_python(self, args: list[str] | None = None, timeout: int = 30) -> dict[str, Any]:
        return self._run([sys.executable, *(args or [])], timeout=timeout)

    def run_pytest(self, args: list[str] | None = None, timeout: int = 30) -> dict[str, Any]:
        return self._run([sys.executable, "-m", "pytest", *(args or [])], timeout=timeout)

    def run_shell(self, command: list[str], timeout: int = 30) -> dict[str, Any]:
        if not command:
            raise ValueError("command must not be empty")
        return self._run(command, timeout=timeout)

    def run_git(self, args: list[str], timeout: int = 30) -> dict[str, Any]:
        return self._run(["git", *args], timeout=timeout)

    def _run(self, command: list[str], timeout: int = 30) -> dict[str, Any]:
        env = os.environ.copy()
        env.setdefault("PYTHONPYCACHEPREFIX", str(self.project_root / ".minidevagent" / "pycache"))
        completed = subprocess.run(
            command,
            cwd=str(self.project_root),
            text=True,
            capture_output=True,
            timeout=timeout,
            shell=False,
            env=env,
        )
        return {
            "command": command,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "return_code": completed.returncode,
            "timeout": timeout,
        }
