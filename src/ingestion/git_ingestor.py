from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any


class GitIngestor:
    """Ingest git commit history into memory records.

    Supports two modes:
      - Live: runs `git log` in the project directory
      - Static: loads from a pre-exported JSON file

    Each commit becomes a memory record with:
      - kind: "commit"
      - text: commit message + changed files
      - entities: [author, ...changed file names]
      - timestamp: commit date
    """

    def ingest_live(self, project_path: str, max_commits: int = 100) -> list[dict[str, Any]]:
        """Run `git log` and parse commits."""
        try:
            result = subprocess.run(
                [
                    "git", "log",
                    f"--max-count={max_commits}",
                    "--format=%H%n%an%n%ad%n%s",
                    "--date=iso-strict",
                    "--name-only",
                ],
                capture_output=True, text=True, timeout=30,
                cwd=project_path,
            )
            if result.returncode != 0:
                return []
            return self._parse_git_log(result.stdout)
        except Exception:
            return []

    def ingest_file(self, file_path: str) -> list[dict[str, Any]]:
        """Load pre-exported commit data from JSON file."""
        data = self._load_json(file_path)
        if not data:
            return []
        return self._normalize_commits(data)

    def _parse_git_log(self, output: str) -> list[dict[str, Any]]:
        """Parse `git log` output into structured records."""
        commits = []
        current: dict[str, Any] | None = None
        for line in output.strip().split("\n"):
            if not line.strip():
                continue
            # New commit starts with a 40-char hex hash
            if re.match(r"^[0-9a-f]{40}$", line.strip()):
                if current:
                    commits.append(self._commit_record(current))
                current = {"hash": line.strip(), "files": []}
            elif current is not None:
                if "author" not in current:
                    current["author"] = line.strip()
                elif "date" not in current:
                    current["date"] = line.strip()
                elif "message" not in current:
                    current["message"] = line.strip()
                else:
                    # Remaining lines are file paths
                    if line.strip():
                        current["files"].append(line.strip())
        if current:
            commits.append(self._commit_record(current))
        return commits

    def _commit_record(self, commit: dict[str, Any]) -> dict[str, Any]:
        message = commit.get("message", "")
        files = commit.get("files", [])
        text = f"[Commit {commit.get('hash', '')[:8]}] {message}"
        if files:
            text += f" | 修改文件: {', '.join(files[:10])}"
        return {
            "kind": "commit",
            "text": text,
            "entities": [
                commit.get("author", ""),
                *[f for f in files if f],
            ],
            "timestamp": commit.get("date", ""),
            "participants": [commit.get("author", "")],
            "hash": commit.get("hash", ""),
        }

    def _normalize_commits(self, data: Any) -> list[dict[str, Any]]:
        if isinstance(data, list):
            commits = data
        elif isinstance(data, dict):
            commits = data.get("commits", data.get("records", []))
        else:
            return []
        records = []
        for c in commits:
            if not isinstance(c, dict):
                continue
            msg = c.get("message", c.get("summary", ""))
            author = c.get("author", "")
            files = c.get("files", c.get("changed_files", []))
            date = c.get("date", c.get("timestamp", ""))
            text = f"[Commit {c.get('hash', '')[:8]}] {msg}"
            if files:
                text += f" | 修改文件: {', '.join(files[:10])}"
            records.append({
                "kind": "commit",
                "text": text,
                "entities": [author, *files],
                "timestamp": date,
                "participants": [author],
                "hash": c.get("hash", ""),
            })
        return records

    def _load_json(self, path: str) -> Any:
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:
            return None
