from __future__ import annotations
import ast
from typing import Any
from .file_tools import read_file


def parse_ast(project_path: str, file_path: str) -> ast.Module:
    source = read_file(project_path, file_path)
    return ast.parse(source)


def get_function_skeleton(project_path: str, file_path: str) -> list[dict[str, Any]]:
    module = parse_ast(project_path, file_path)
    skeletons: list[dict[str, Any]] = []
    for node in module.body:
        if isinstance(node, ast.FunctionDef):
            args = [arg.arg for arg in node.args.args]
            skeletons.append(
                {
                    "type": "function",
                    "name": node.name,
                    "signature": f"def {node.name}({', '.join(args)})",
                    "docstring": ast.get_docstring(node) or "",
                }
            )
        elif isinstance(node, ast.ClassDef):
            func_names = [n.name for n in node.body if isinstance(n, ast.FunctionDef)]
            skeletons.append(
                {
                    "type": "class",
                    "name": node.name,
                    "members": func_names,
                    "docstring": ast.get_docstring(node) or "",
                }
            )
    return skeletons


def get_imports(project_path: str, file_path: str) -> list[str]:
    module = parse_ast(project_path, file_path)
    imports: list[str] = []
    for node in module.body:
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module_name = "." * node.level + (node.module or "")
            imports.append(module_name)
    return imports


def find_symbol_definitions(project_path: str, file_paths: list[str], symbol: str) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for file_path in file_paths:
        try:
            module = parse_ast(project_path, file_path)
        except SyntaxError:
            continue
        for node in ast.walk(module):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == symbol:
                matches.append(
                    {
                        "path": file_path,
                        "symbol": symbol,
                        "type": "class" if isinstance(node, ast.ClassDef) else "function",
                        "line": node.lineno,
                    }
                )
    return matches


def extract_symbol_snippet(project_path: str, file_path: str, symbol: str) -> str:
    source = read_file(project_path, file_path)
    lines = source.splitlines()
    try:
        module = ast.parse(source)
    except SyntaxError:
        return extract_relevant_snippet(project_path, file_path, symbol)
    for node in ast.walk(module):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == symbol:
            start = max(node.lineno - 1, 0)
            end = getattr(node, "end_lineno", node.lineno)
            return "\n".join(lines[start:end]).strip()
    return extract_relevant_snippet(project_path, file_path, symbol)


def extract_symbol_snippet_strict(project_path: str, file_path: str, symbol: str) -> str:
    source = read_file(project_path, file_path)
    lines = source.splitlines()
    try:
        module = ast.parse(source)
    except SyntaxError:
        return ""
    for node in ast.walk(module):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == symbol:
            start = max(node.lineno - 1, 0)
            end = getattr(node, "end_lineno", node.lineno)
            return "\n".join(lines[start:end]).strip()
    return ""


def extract_relevant_snippet(project_path: str, file_path: str, keyword: str) -> str:
    text = read_file(project_path, file_path)
    query_terms = [term.lower() for term in keyword.replace("(", " ").replace(")", " ").split() if len(term) > 2]
    lines = text.splitlines()
    for index, line in enumerate(lines):
        lower_line = line.lower()
        if any(term in lower_line for term in query_terms):
            start = max(index - 2, 0)
            end = min(index + 3, len(lines))
            snippet = "\n".join(lines[start:end])
            return snippet.strip()
    return lines[:10] and "\n".join(lines[:10]).strip() or ""
