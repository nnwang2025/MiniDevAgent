from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class IssueIngestor:
    """Ingest issue/bug tracking data into memory records.

    Supports JSON files with issue records. Each issue becomes
    a memory record with kind="issue", including the resolution
    if available.

    Expected JSON format:
      [
        {
          "id": "ISS-42",
          "title": "login() returns True for empty password",
          "description": "...",
          "status": "resolved",
          "resolution": "Added empty-string validation in login()",
          "author": "alice",
          "assignee": "bob",
          "created": "2025-03-15T10:00:00",
          "resolved": "2025-03-16T14:30:00",
          "labels": ["bug", "security"],
          "related_files": ["auth.py"],
          "comments": [...]
        }
      ]
    """

    def ingest_file(self, file_path: str) -> list[dict[str, Any]]:
        data = self._load_json(file_path)
        if not data:
            return []
        return self._normalize_issues(data)

    def _normalize_issues(self, data: Any) -> list[dict[str, Any]]:
        items = data if isinstance(data, list) else data.get("issues", data.get("records", []))
        if not isinstance(items, list):
            return []

        records = []
        for issue in items:
            if not isinstance(issue, dict):
                continue

            title = issue.get("title", "")
            desc = issue.get("description", "")
            resolution = issue.get("resolution", "")
            status = issue.get("status", "open")
            issue_id = issue.get("id", "")
            author = issue.get("author", "")
            assignee = issue.get("assignee", "")
            labels = issue.get("labels", [])
            related = issue.get("related_files", [])
            comments = issue.get("comments", [])

            # Build a rich text representation
            parts = [f"[Issue {issue_id}] {title}"]
            if desc:
                parts.append(f"描述: {desc[:200]}")
            parts.append(f"状态: {status} | 报告人: {author} | 负责人: {assignee}")
            if labels:
                parts.append(f"标签: {', '.join(labels)}")
            if resolution:
                parts.append(f"解决方案: {resolution}")
            if related:
                parts.append(f"相关文件: {', '.join(related)}")
            if comments:
                comment_texts = []
                for c in comments[-3:]:  # last 3 comments
                    if isinstance(c, dict):
                        comment_texts.append(f"{c.get('author', '?')}: {c.get('text', '')[:100]}")
                    else:
                        comment_texts.append(str(c)[:100])
                parts.append(f"讨论: {' | '.join(comment_texts)}")

            records.append({
                "kind": "issue",
                "text": "\n".join(parts),
                "entities": [author, assignee, *related, *labels],
                "timestamp": issue.get("resolved", issue.get("created", "")),
                "participants": [author, assignee],
                "issue_id": issue_id,
                "status": status,
            })

        return records

    def _load_json(self, path: str) -> Any:
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:
            return None
