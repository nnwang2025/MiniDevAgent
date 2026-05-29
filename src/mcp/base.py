from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


BLOCKED_PARTS = {".env", ".git", ".venv", "__pycache__"}


class MCPError(Exception):
    pass


@dataclass
class MCPResult:
    ok: bool
    server: str
    tool: str
    data: Any = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "server": self.server,
            "tool": self.tool,
            "data": self.data,
            "error": self.error,
        }


class MCPServer:
    name = "base"

    def __init__(self, project_root: str) -> None:
        self.project_root = Path(project_root).resolve()

    def call(self, tool: str, **kwargs: Any) -> MCPResult:
        method = getattr(self, tool, None)
        if not callable(method):
            return MCPResult(False, self.name, tool, error=f"Unknown MCP tool: {tool}")
        try:
            data = method(**kwargs)
            return MCPResult(True, self.name, tool, data=data)
        except Exception as exc:
            return MCPResult(False, self.name, tool, error=str(exc))

    def _safe_path(self, relative_path: str = ".") -> Path:
        path = Path(relative_path)
        candidate = path.resolve() if path.is_absolute() else (self.project_root / path).resolve()
        if self.project_root != candidate and self.project_root not in candidate.parents:
            raise MCPError("Access denied: path is outside project root.")
        relative_parts = candidate.relative_to(self.project_root).parts if candidate != self.project_root else ()
        if any(part in BLOCKED_PARTS for part in relative_parts):
            raise MCPError(f"Access denied: blocked path part in {relative_path}.")
        return candidate


class MCPClient:
    def __init__(self, servers: dict[str, MCPServer]) -> None:
        self.servers = servers

    def call(self, server: str, tool: str, **kwargs: Any) -> dict[str, Any]:
        if server not in self.servers:
            return MCPResult(False, server, tool, error=f"Unknown MCP server: {server}").to_dict()
        return self.servers[server].call(tool, **kwargs).to_dict()
