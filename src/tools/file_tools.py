from __future__ import annotations
import os
from pathlib import Path
from typing import Any

from .safety_tools import normalize_project_path, resolve_safe_path


IGNORED_DIRS = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
}


def list_files(project_path: str, extensions: tuple[str, ...] = (".py",)) -> list[str]:
    root = Path(normalize_project_path(project_path))
    files: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [dirname for dirname in dirnames if dirname not in IGNORED_DIRS and not dirname.startswith(".")]
        for filename in filenames:
            if filename.startswith("."):
                continue
            if Path(filename).suffix in extensions:
                files.append(str(Path(dirpath, filename).relative_to(root)))
    files.sort()
    return files


def read_file(project_path: str, file_path: str) -> str:
    path = resolve_safe_path(project_path, file_path)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"文件不存在：{file_path}")
    return path.read_text(encoding="utf-8")


def search_by_filename(project_path: str, name: str) -> list[str]:
    candidates: list[str] = []
    for relative in list_files(project_path):
        if name.lower() in Path(relative).name.lower():
            candidates.append(relative)
    return candidates


def summarize_file(project_path: str, file_path: str) -> str:
    text = read_file(project_path, file_path)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return "空文件。"
    summary_lines = lines[:5]
    first_line = summary_lines[0]
    if first_line.startswith("#!"):
        summary_lines = summary_lines[1:]
    summary = " ".join(summary_lines[:4])
    return summary.strip()[:400]


def build_project_index(project_path: str) -> dict[str, Any]:
    files = list_files(project_path)
    index: dict[str, Any] = {
        "root": normalize_project_path(project_path),
        "files": [],
        "count": len(files),
        "tree": build_file_tree(files),
    }
    for relative in files:
        index["files"].append(
            {
                "path": relative,
                "name": Path(relative).name,
                "summary": summarize_file(project_path, relative),
            }
        )
    return index


def build_file_tree(files: list[str]) -> str:
    tree: dict[str, Any] = {}
    for relative in files:
        cursor = tree
        parts = Path(relative).parts
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[parts[-1]] = None

    def render(node: dict[str, Any], depth: int = 0) -> list[str]:
        lines: list[str] = []
        for name in sorted(node):
            value = node[name]
            lines.append(f"{'  ' * depth}{name}/" if isinstance(value, dict) else f"{'  ' * depth}{name}")
            if isinstance(value, dict):
                lines.extend(render(value, depth + 1))
        return lines

    return "\n".join(render(tree))
