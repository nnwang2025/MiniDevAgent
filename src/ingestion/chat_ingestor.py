from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ChatIngestor:
    """Ingest team chat/discussion data into memory records.

    Expected JSON format:
      [
        {
          "id": "msg-001",
          "channel": "backend-team",
          "author": "alice",
          "text": "Why does login() accept empty passwords?",
          "timestamp": "2025-03-15T09:30:00",
          "mentions": ["bob"],
          "thread_replies": [
            {"author": "bob", "text": "That was added for legacy API compatibility.", "timestamp": "..."},
            {"author": "alice", "text": "We should fix that. Create an issue?", "timestamp": "..."}
          ]
        }
      ]

    The ingestor creates records for both individual messages and
    threaded discussions. Thread replies are merged into a single
    episodic record for better retrieval.
    """

    def ingest_file(self, file_path: str) -> list[dict[str, Any]]:
        data = self._load_json(file_path)
        if not data:
            return []
        return self._normalize_messages(data)

    def _normalize_messages(self, data: Any) -> list[dict[str, Any]]:
        items = data if isinstance(data, list) else data.get("messages", data.get("records", []))
        if not isinstance(items, list):
            return []

        records = []
        for msg in items:
            if not isinstance(msg, dict):
                continue

            author = msg.get("author", "")
            text = msg.get("text", "")
            channel = msg.get("channel", "")
            mentions = msg.get("mentions", [])
            replies = msg.get("thread_replies", [])

            # Individual message record
            records.append({
                "kind": "chat",
                "text": f"[Chat #{channel}] {author}: {text}",
                "entities": [author, *mentions, channel],
                "timestamp": msg.get("timestamp", ""),
                "participants": [author, *mentions],
                "channel": channel,
            })

            # Thread discussion → merged episodic record
            if replies:
                participants = {author}
                all_text = [f"{author}: {text}"]
                for reply in replies:
                    if isinstance(reply, dict):
                        ra = reply.get("author", "")
                        rt = reply.get("text", "")
                        participants.add(ra)
                        all_text.append(f"{ra}: {rt}")

                records.append({
                    "kind": "discussion",
                    "text": f"[Discussion #{channel}] " + " | ".join(all_text),
                    "entities": list(participants),
                    "timestamp": msg.get("timestamp", ""),
                    "participants": list(participants),
                    "channel": channel,
                    "topic": text[:80],
                })

        return records

    def _load_json(self, path: str) -> Any:
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:
            return None
