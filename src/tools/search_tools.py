from __future__ import annotations
import re
from typing import Any
from .file_tools import list_files, read_file


def search_code(project_path: str, keyword: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    needle = keyword.lower()
    for relative in list_files(project_path):
        text = read_file(project_path, relative)
        for idx, line in enumerate(text.splitlines(), start=1):
            if needle in line.lower():
                results.append(
                    {
                        "path": relative,
                        "line": idx,
                        "snippet": line.strip(),
                    }
                )
                break
    return results


def search_imports(project_path: str, module_name: str) -> list[str]:
    matches: list[str] = []
    for relative in list_files(project_path):
        text = read_file(project_path, relative)
        if re.search(rf"\b(import|from)\s+{re.escape(module_name)}\b", text):
            matches.append(relative)
    return matches


def search_references(project_path: str, symbol: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    pattern = re.compile(rf"\b{re.escape(symbol)}\b", re.IGNORECASE)
    for relative in list_files(project_path):
        text = read_file(project_path, relative)
        for idx, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                results.append({"path": relative, "line": idx, "snippet": line.strip()})
    return results


def trace_file_dependencies(project_path: str, seed_paths: list[str]) -> dict[str, list[str]]:
    files = set(list_files(project_path))
    stems = {relative.rsplit(".", 1)[0].replace("\\", ".").replace("/", "."): relative for relative in files}
    trace: dict[str, list[str]] = {}
    for relative in seed_paths:
        if relative not in files:
            continue
        text = read_file(project_path, relative)
        dependencies: list[str] = []
        for match in re.finditer(r"^\s*from\s+(\.+[\w\.]+|[\w\.]+)\s+import\s+(.+)$|^\s*import\s+([\w\.]+)", text, re.MULTILINE):
            module = match.group(1) or match.group(3) or ""
            normalized = module.lstrip(".")
            for stem, path in stems.items():
                if stem.endswith(normalized) and path != relative:
                    dependencies.append(path)
        trace[relative] = list(dict.fromkeys(dependencies))
    return trace
