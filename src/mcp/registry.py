from __future__ import annotations

from .base import MCPClient
from .code_analysis_server import CodeAnalysisMCPServer
from .filesystem_server import FilesystemMCPServer
from .terminal_server import TerminalMCPServer


def create_mcp_client(project_root: str) -> MCPClient:
    return MCPClient(
        {
            "filesystem": FilesystemMCPServer(project_root),
            "terminal": TerminalMCPServer(project_root),
            "code_analysis": CodeAnalysisMCPServer(project_root),
        }
    )
