from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


class LogIngestor:
    """Ingest deployment, error, and runtime logs into memory records.

    Expected JSON format:
      [
        {
          "id": "log-001",
          "level": "ERROR",
          "service": "auth-api",
          "message": "AttributeError: 'NoneType' object has no attribute 'get'",
          "traceback": "Traceback (most recent call last):\\n  File \"auth.py\", line 42, in login\\n    ...",
          "timestamp": "2025-03-15T10:05:00",
          "environment": "production",
          "resolved_by_commit": "abc12345"
        }
      ]

    Logs are ingested as "error" or "log" kind records. Traceback
    entries are specially tagged for ProblemTraceSkill to find.
    """

    def ingest_file(self, file_path: str) -> list[dict[str, Any]]:
        data = self._load_json(file_path)
        if not data:
            return []
        return self._normalize_logs(data)

    def _normalize_logs(self, data: Any) -> list[dict[str, Any]]:
        items = data if isinstance(data, list) else data.get("logs", data.get("records", []))
        if not isinstance(items, list):
            return []

        records = []
        for log in items:
            if not isinstance(log, dict):
                continue

            level = log.get("level", "INFO").upper()
            service = log.get("service", "")
            message = log.get("message", "")
            traceback = log.get("traceback", "")
            env = log.get("environment", "")
            resolved = log.get("resolved_by_commit", "")
            timestamp = log.get("timestamp", "")

            # Determine kind based on level
            if level in {"ERROR", "CRITICAL", "FATAL"}:
                kind = "error"
            elif "traceback" in message.lower() or traceback:
                kind = "traceback"
            elif level == "WARNING":
                kind = "log"
            else:
                kind = "log"

            # Build rich text
            text_parts = [f"[{level}] [{service}] {message}"]
            if traceback:
                # Extract key lines from traceback
                tb_lines = traceback.strip().split("\n")
                key_lines = [l for l in tb_lines if "File" in l or "Error" in l or "Exception" in l]
                text_parts.append("Traceback: " + " | ".join(key_lines[-5:]))
            if resolved:
                text_parts.append(f"已由 commit {resolved[:8]} 修复")
            if env:
                text_parts.append(f"环境: {env}")

            # Extract entities from traceback
            entities = [service, env]
            if traceback:
                file_matches = re.findall(r'File "([^"]+)"', traceback)
                entities.extend(file_matches)
                func_matches = re.findall(r'in ([\w_]+)\b', traceback)
                entities.extend(func_matches)

            records.append({
                "kind": kind,
                "text": "\n".join(text_parts),
                "entities": entities,
                "timestamp": timestamp,
                "participants": [],
                "level": level,
                "service": service,
                "environment": env,
                "resolved_by": resolved,
            })

        return records

    def _load_json(self, path: str) -> Any:
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:
            return None
