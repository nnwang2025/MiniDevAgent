from __future__ import annotations
import re
from typing import Any
from pathlib import Path
from ..tools.ast_tools import find_symbol_definitions
from ..tools.file_tools import build_project_index, list_files, search_by_filename
from ..tools.search_tools import search_code, search_references, trace_file_dependencies
from ..memory.code_index import CodeIndex


STOPWORDS = {
    "about", "where", "what", "when", "which", "would", "could", "should", "does",
    "this", "that", "with", "from", "into", "through", "implemented", "implementation", "explain",
    "analyze", "function", "method", "class", "file", "files", "project", "codebase",
    "the", "app",
}


class ExplorerAgent:
    def explore(self, project_path: str, question: str, plan: dict[str, Any] | None = None) -> dict[str, Any]:
        plan = plan or {}
        task_type = plan.get("task_type", "general_question")
        index_data = build_project_index(project_path)
        code_index = CodeIndex(root=index_data["root"], files=index_data["files"])
        keywords = self._dedupe((plan.get("search_tokens") or []) + self._keywords(question))
        symbols = self._dedupe((plan.get("target_symbols") or []) + self._symbols(question))
        project_files = list_files(project_path)
        file_mentions = self._dedupe((plan.get("candidate_files_hint") or []) + self._file_mentions(project_path, question))
        file_mentions = self._existing_files(file_mentions, project_files)

        if task_type == "project_overview":
            overview_candidates = file_mentions or self._overview_candidates(project_files, plan.get("target_area", ""))
            return {
                "index": index_data,
                "candidates": overview_candidates,
                "search_tokens": keywords[:6],
                "symbols": symbols,
                "file_tree": index_data.get("tree", ""),
                "task_type": task_type,
                "target_area": plan.get("target_area", ""),
                "dependency_trace": trace_file_dependencies(project_path, overview_candidates) if overview_candidates else {},
            }

        if task_type == "component_overview":
            candidate_paths = file_mentions or self._search_candidates(project_path, code_index, keywords)
            candidate_paths = list(dict.fromkeys(candidate_paths))
            return {
                "index": index_data,
                "candidates": candidate_paths,
                "search_tokens": keywords[:6],
                "symbols": symbols,
                "file_tree": index_data.get("tree", ""),
                "task_type": task_type,
                "target_area": plan.get("target_area", ""),
                "dependency_trace": {},
            }

        if task_type == "function_analysis":
            candidate_paths = self._function_candidates(project_path, file_mentions, symbols, keywords)
        elif task_type in {"architecture_flow", "dependency_trace"}:
            candidate_paths = self._architecture_candidates(project_path, code_index, file_mentions, symbols, keywords)
        else:
            candidate_paths = self._search_candidates(project_path, code_index, keywords)

        candidate_paths = list(dict.fromkeys(candidate_paths))
        return {
            "index": index_data,
            "candidates": candidate_paths,
            "search_tokens": keywords[:4],
            "symbols": symbols,
            "file_tree": index_data.get("tree", ""),
            "task_type": task_type,
            "dependency_trace": trace_file_dependencies(project_path, candidate_paths) if task_type in {"architecture_flow", "dependency_trace"} else {},
        }

    def _keywords(self, question: str) -> list[str]:
        words = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", question)
        return [word for word in words if len(word) > 2 and word.lower() not in STOPWORDS]

    def _symbols(self, question: str) -> list[str]:
        explicit = re.findall(r"`?([A-Za-z_][A-Za-z0-9_]*)\s*\(\)`?", question)
        quoted = re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", question)
        return list(dict.fromkeys(explicit + quoted))

    def _file_mentions(self, project_path: str, question: str) -> list[str]:
        mentioned = set(re.findall(r"[\w/\\.-]+\.py", question))
        files = list_files(project_path)
        matches: list[str] = []
        for file_path in files:
            if file_path in mentioned or Path(file_path).name in mentioned:
                matches.append(file_path)
        return matches

    def _function_candidates(self, project_path: str, file_mentions: list[str], symbols: list[str], keywords: list[str]) -> list[str]:
        search_scope = file_mentions or list_files(project_path)
        for symbol in symbols + keywords[:2]:
            definitions = find_symbol_definitions(project_path, search_scope, symbol)
            if definitions:
                return [match["path"] for match in definitions]
        if file_mentions:
            return file_mentions
        return self._search_candidates(project_path, CodeIndex(root=project_path, files=build_project_index(project_path)["files"]), keywords)[:2]

    def _architecture_candidates(self, project_path: str, code_index: CodeIndex, file_mentions: list[str], symbols: list[str], keywords: list[str]) -> list[str]:
        candidates = list(file_mentions)
        files = list_files(project_path)
        if "main.py" in files:
            candidates.insert(0, "main.py")
        for symbol in symbols + keywords[:2]:
            candidates.extend(match["path"] for match in find_symbol_definitions(project_path, files, symbol))
            candidates.extend(result["path"] for result in search_references(project_path, symbol))
        if not candidates:
            candidates.extend(self._search_candidates(project_path, code_index, keywords)[:3])
        dependencies = trace_file_dependencies(project_path, list(dict.fromkeys(candidates)))
        for paths in dependencies.values():
            candidates.extend(paths)
        return candidates[:6]

    def _search_candidates(self, project_path: str, code_index: CodeIndex, keywords: list[str]) -> list[str]:
        scores: dict[str, int] = {}
        for token in keywords[:4]:
            for entry in code_index.find_by_keyword(token):
                scores[entry["path"]] = scores.get(entry["path"], 0) + 2
            for path in search_by_filename(project_path, token):
                scores[path] = scores.get(path, 0) + 3
            for result in search_code(project_path, token):
                scores[result["path"]] = scores.get(result["path"], 0) + 5
        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        return [path for path, _ in ranked[:4]]

    def _overview_candidates(self, project_files: list[str], target_area: str) -> list[str]:
        if target_area and target_area != "project":
            prefix = f"src\\{target_area}\\"
            return [path for path in project_files if path.startswith(prefix)][:8]
        preferred = [
            "src\\main.py",
            "src\\runtime\\harness.py",
            "src\\agents\\planner_agent.py",
            "src\\agents\\explorer_agent.py",
            "src\\agents\\analyzer_agent.py",
            "src\\agents\\context_agent.py",
            "src\\agents\\answer_agent.py",
            "src\\tools\\file_tools.py",
            "src\\tools\\ast_tools.py",
            "src\\api\\routes.py",
        ]
        return [path for path in preferred if path in project_files]

    def _existing_files(self, hints: list[str], project_files: list[str]) -> list[str]:
        by_normalized = {path.replace("/", "\\").lower(): path for path in project_files}
        result: list[str] = []
        for hint in hints:
            normalized = hint.replace("/", "\\").lower()
            if normalized in by_normalized:
                result.append(by_normalized[normalized])
                continue
            name_matches = [path for path in project_files if path.lower().endswith("\\" + normalized) or path.lower() == normalized]
            result.extend(name_matches)
        return self._dedupe(result)

    def _dedupe(self, values: list[str]) -> list[str]:
        result: list[str] = []
        for value in values:
            item = value.strip()
            if item and item not in result:
                result.append(item)
        return result
