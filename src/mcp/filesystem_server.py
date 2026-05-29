from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from .base import MCPServer


class FilesystemMCPServer(MCPServer):
    name = "filesystem"

    def list_dir(self, path: str = ".") -> dict[str, Any]:
        root = self._safe_path(path)
        entries = []
        for item in sorted(root.iterdir(), key=lambda value: value.name.lower()):
            if item.name in {".env", ".git", ".venv", "__pycache__"}:
                continue
            entries.append({"name": item.name, "path": str(item.relative_to(self.project_root)), "type": "dir" if item.is_dir() else "file"})
        return {"path": str(root.relative_to(self.project_root)) if root != self.project_root else ".", "entries": entries}

    def read_file(self, path: str) -> dict[str, Any]:
        file_path = self._safe_path(path)
        text = file_path.read_text(encoding="utf-8")
        return {"path": str(file_path.relative_to(self.project_root)), "content": text, "size": len(text)}

    def write_file(self, path: str, content: str) -> dict[str, Any]:
        file_path = self._safe_path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return {"path": str(file_path.relative_to(self.project_root)), "bytes": len(content.encode("utf-8"))}

    def search_file(self, pattern: str, glob: str = "*.py") -> dict[str, Any]:
        regex = re.compile(pattern, re.IGNORECASE)
        matches = []
        for file_path in self.project_root.rglob(glob):
            try:
                safe = self._safe_path(str(file_path))
            except Exception:
                continue
            if not safe.is_file():
                continue
            text = safe.read_text(encoding="utf-8", errors="ignore")
            for line_no, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    matches.append({"path": str(safe.relative_to(self.project_root)), "line": line_no, "snippet": line.strip()})
                    break
        return {"pattern": pattern, "matches": matches}

    def replace_text(self, path: str, old: str, new: str, count: int = 1) -> dict[str, Any]:
        file_path = self._safe_path(path)
        text = file_path.read_text(encoding="utf-8")
        occurrences = text.count(old)
        if occurrences <= 0:
            return {"path": str(file_path.relative_to(self.project_root)), "replaced": 0, "content": text}
        updated = text.replace(old, new, count)
        return {"path": str(file_path.relative_to(self.project_root)), "replaced": min(occurrences, count), "content": updated}
