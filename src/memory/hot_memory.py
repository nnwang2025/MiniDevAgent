from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .redis_backend import RedisMemoryBackend


@dataclass
class HotMemory:
    """Session memory with optional Redis persistence.

    Dual-write mode: when a Redis backend is bound, all writes go to
    both local memory (fast read) and Redis (persistence + multi-process).
    Reads use local memory for zero-latency access.

    Without Redis: pure in-memory dict/List — zero dependencies.
    """

    recent_conversation: list[str] = field(default_factory=list)
    active_files: list[str] = field(default_factory=list)
    tool_outputs: list[dict[str, Any]] = field(default_factory=list)
    _redis: RedisMemoryBackend | None = field(default=None, repr=False)

    # ── Redis binding ────────────────────────────────────────────

    def bind_redis(self, backend: RedisMemoryBackend) -> None:
        """Bind a Redis backend for persistence + multi-process sharing."""
        self._redis = backend

    @property
    def has_redis(self) -> bool:
        return self._redis is not None and self._redis.is_redis

    # ── Storage (dual-write) ─────────────────────────────────────

    def add_message(self, message: str) -> None:
        self.recent_conversation.append(message)
        if len(self.recent_conversation) > 20:
            self.recent_conversation.pop(0)
        if self._redis:
            self._redis.push_conversation(message)

    def add_file(self, file_path: str) -> None:
        if file_path not in self.active_files:
            self.active_files.append(file_path)
        if len(self.active_files) > 30:
            self.active_files = self.active_files[-30:]
        if self._redis:
            self._redis.push_file(file_path)

    def add_output(self, output: dict[str, Any]) -> None:
        self.tool_outputs.append(output)
        if len(self.tool_outputs) > 50:
            self.tool_outputs.pop(0)
        if self._redis:
            self._redis.push_output(output)

    # ── Retrieval ────────────────────────────────────────────────

    def get_recent_context(self, n: int = 5) -> list[str]:
        """Get recent conversation messages. Falls back to Redis if local empty."""
        msgs = self.recent_conversation[-n:] if self.recent_conversation else []
        if not msgs and self._redis:
            msgs = self._redis.get_conversation(n)
        return msgs

    def get_active_files(self, n: int = 10) -> list[str]:
        """Get recently accessed files."""
        files = self.active_files[-n:] if self.active_files else []
        if not files and self._redis:
            files = self._redis.get_files(n)
        return files

    def find_relevant_messages(self, query: str, top_k: int = 3) -> list[str]:
        """Find conversation messages most relevant to the query."""
        candidates = self.recent_conversation
        if not candidates and self._redis:
            candidates = self._redis.get_conversation(20)
        if not candidates:
            return []

        query_terms = self._terms(query)
        if not query_terms:
            return candidates[-top_k:]

        scored = [(len(set(query_terms) & set(self._terms(m))) / max(1, len(set(query_terms))), m) for m in candidates]
        scored = [(s, m) for s, m in scored if s > 0]
        scored.sort(key=lambda x: -x[0])
        return [m for _, m in scored[:top_k]]

    def find_relevant_files(self, query: str, top_k: int = 5) -> list[str]:
        """Find active files most relevant to the query."""
        candidates = self.active_files
        if not candidates and self._redis:
            candidates = self._redis.get_files(30)
        if not candidates:
            return []

        query_terms = self._terms(query)
        if not query_terms:
            return candidates[-top_k:]

        scored = [(len(set(query_terms) & set(self._terms(f.replace("\\", "/")))) / max(1, len(set(query_terms))), f) for f in candidates]
        scored = [(s, f) for s, f in scored if s > 0]
        scored.sort(key=lambda x: -x[0])
        return [f for _, f in scored[:top_k]]

    def get_conversation_summary(self) -> str:
        parts = []
        convos = self.recent_conversation
        if not convos and self._redis:
            convos = self._redis.get_conversation(20)
        if convos:
            parts.append(f"最近对话 ({len(convos)} 条): " + " | ".join(convos[-5:]))
        if self.active_files:
            parts.append(f"活跃文件: {', '.join(self.active_files[-8:])}")
        return "\n".join(parts)

    # ── Helpers ──────────────────────────────────────────────────

    def _terms(self, text: str) -> list[str]:
        ascii_terms = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text.lower())
        chinese = re.sub(r"[^一-鿿]", "", text)
        chinese_terms = [chinese[i:i + 2] for i in range(max(0, len(chinese) - 1))]
        return ascii_terms + chinese_terms
