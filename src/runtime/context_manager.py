from __future__ import annotations

from typing import Any

from ..memory.cold_memory import ColdMemory
from ..memory.hot_memory import HotMemory


class ContextManager:
    def __init__(self, hot_memory: HotMemory, cold_memory: ColdMemory) -> None:
        self.hot = hot_memory
        self.cold = cold_memory

    def update_conversation(self, text: str) -> None:
        self.hot.add_message(text)

    def register_file(self, file_path: str) -> None:
        self.hot.add_file(file_path)

    def compress(self, task_type: str, skill_result: dict[str, Any], question: str) -> dict[str, Any]:
        observation = skill_result.get("observations", {})
        kept: dict[str, Any] = {"question": question, "task_type": task_type}
        dropped: list[str] = []

        if task_type == "project_overview":
            kept.update(
                {
                    "file_tree": observation.get("file_tree", ""),
                    "module_summaries": observation.get("module_summaries", [])[:12],
                    "key_functions": observation.get("key_functions", [])[:20],
                    "entry_files": observation.get("entry_files", [])[:8],
                }
            )
            dropped = ["full_file_contents", "unselected_modules"]
        elif task_type == "function_analysis":
            kept.update(
                {
                    "target": observation.get("target"),
                    "definition": observation.get("definition"),
                    "signature": observation.get("signature"),
                    "returns": observation.get("returns", []),
                    "calls": observation.get("calls", []),
                    "snippet": observation.get("snippet", ""),
                }
            )
            dropped = ["other_functions", "full_project_tree"]
        elif task_type == "dependency_trace":
            kept.update(
                {
                    "target": observation.get("target"),
                    "definitions": observation.get("definitions", []),
                    "imports": observation.get("imports", []),
                    "calls": observation.get("calls", []),
                    "snippets": observation.get("snippets", [])[:8],
                }
            )
            dropped = ["prompts_docs_tests", "unrelated_references"]
        elif task_type == "entrypoint_analysis":
            kept.update(
                {
                    "entry_files": observation.get("entry_files", []),
                    "entries": observation.get("entries", []),
                }
            )
            dropped = ["non_entry_modules", "full_file_contents"]
        elif task_type in {"code_edit", "code_fix"}:
            kept.update(
                {
                    "target": observation.get("target"),
                    "patch_file": (observation.get("patch") or {}).get("file"),
                    "diff": (observation.get("patch") or {}).get("diff"),
                    "verification_plan": observation.get("verification_plan", []),
                }
            )
            dropped = ["auto_apply", "unrelated_files", "full_project_context"]
        elif task_type == "memory_recall":
            kept.update(
                {
                    "memory_sources": observation.get("memory_sources", []),
                    "chunks": observation.get("chunks", [])[:5],
                    "raw_evidence": observation.get("raw_evidence", [])[:5],
                }
            )
            dropped = ["hot_memory_messages", "low_score_memory"]
        elif task_type == "problem_trace":
            kept.update(
                {
                    "problem_sources": observation.get("problem_sources", []),
                    "traceback_or_log_evidence": observation.get("records", [])[:5],
                }
            )
            dropped = ["non_error_memory", "generic_suggestions"]
        else:
            kept.update({"evidence": skill_result.get("evidence", [])[:8]})
            dropped = ["untyped_context"]

        return {
            "context": kept,
            "trace": {
                "strategy": task_type,
                "kept": [key for key, value in kept.items() if value],
                "dropped": dropped,
            },
        }
