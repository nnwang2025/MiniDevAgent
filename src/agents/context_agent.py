from __future__ import annotations
from typing import Any


class ContextAgent:
    def compress_context(self, analysis: dict[str, Any], recent_notes: list[str]) -> dict[str, Any]:
        file_summaries = []
        skeletons = []
        snippets = []
        for file_path, metadata in analysis.get("files", {}).items():
            file_summaries.append(f"{file_path}: {metadata.get('summary', '')}")
            skeletons.append(f"{file_path}: {len(metadata.get('skeleton', []))} definitions")
            snippet = metadata.get("snippet")
            if snippet:
                snippets.append(f"{file_path}: {snippet}")
        return {
            "task_type": analysis.get("task_type", "general_question"),
            "summary": "\n".join(file_summaries[:5]),
            "skeletons": "\n".join(skeletons[:5]),
            "snippets": "\n".join(snippets[:5]),
            "file_tree": analysis.get("file_tree", ""),
            "dependency_trace": analysis.get("dependency_trace", {}),
            "project_index": analysis.get("project_index", {}),
            "conversation_history": "\n".join(recent_notes[-5:]),
        }
