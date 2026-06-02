"""Redis-backed memory with graceful degradation.

Tier model (progressive enhancement):
  - Redis available → persistent, multi-process shared memory
  - Redis unavailable → in-memory dict (zero-dependency fallback)

Session isolation via key prefix:  minidevagent:{session_id}:*
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

try:
    import redis as redis_py
    HAS_REDIS = True
except ImportError:
    redis_py = None  # type: ignore
    HAS_REDIS = False

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
REDIS_ENABLED = os.getenv("REDIS_ENABLED", "").lower() in {"1", "true", "yes"}

# ── Key schemas ───────────────────────────────────────────────────

KEY_PREFIX = "minidevagent"
KEY_CONVERSATION = "{prefix}:{sid}:conversation"     # List (LPUSH / LRANGE)
KEY_ACTIVE_FILES  = "{prefix}:{sid}:active_files"    # List
KEY_TOOL_OUTPUTS  = "{prefix}:{sid}:tool_outputs"    # List (JSON-encoded)
KEY_METADATA      = "{prefix}:{sid}:metadata"        # Hash
KEY_RETRIEVAL_CACHE = "{prefix}:{sid}:cache:{query_hash}"  # String (JSON)


class RedisMemoryBackend:
    """Redis-backed hot memory with in-memory fallback.

    Usage (transparent):
        backend = RedisMemoryBackend.connect(session_id="s1")
        backend.push_conversation("user message")     # auto-pick Redis or memory
        msgs = backend.get_conversation(n=5)          # transparent retrieval
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._redis: Any = None
        self._fallback_conversation: list[str] = []
        self._fallback_files: list[str] = []
        self._fallback_outputs: list[dict[str, Any]] = []
        self._fallback_metadata: dict[str, Any] = {}

    @classmethod
    def connect(cls, session_id: str) -> "RedisMemoryBackend":
        backend = cls(session_id)
        if HAS_REDIS and REDIS_ENABLED:
            try:
                backend._redis = redis_py.from_url(REDIS_URL, decode_responses=True)
                backend._redis.ping()
                logger.info("Redis connected: %s (session=%s)", REDIS_URL, session_id)
            except Exception as exc:
                logger.warning("Redis unavailable (%s), falling back to in-memory", exc)
                backend._redis = None
        else:
            logger.info("Redis disabled or not installed, using in-memory backend")
        return backend

    @property
    def is_redis(self) -> bool:
        return self._redis is not None

    def health(self) -> dict[str, Any]:
        """Health check for observability."""
        if self._redis:
            try:
                self._redis.ping()
                info = self._redis.info("memory")
                return {
                    "backend": "redis",
                    "connected": True,
                    "used_memory_human": info.get("used_memory_human", "?"),
                    "session_id": self.session_id,
                }
            except Exception as exc:
                return {"backend": "redis", "connected": False, "error": str(exc)}
        return {"backend": "in_memory", "connected": True, "session_id": self.session_id}

    # ── Conversation ──────────────────────────────────────────────

    def push_conversation(self, message: str) -> None:
        if self._redis:
            key = KEY_CONVERSATION.format(prefix=KEY_PREFIX, sid=self.session_id)
            self._redis.lpush(key, message)
            self._redis.ltrim(key, 0, 19)  # keep last 20
        else:
            self._fallback_conversation.append(message)
            if len(self._fallback_conversation) > 20:
                self._fallback_conversation.pop(0)

    def get_conversation(self, n: int = 5) -> list[str]:
        if self._redis:
            key = KEY_CONVERSATION.format(prefix=KEY_PREFIX, sid=self.session_id)
            items = self._redis.lrange(key, 0, n - 1)
            return list(reversed(items))  # lpush puts newest first
        return self._fallback_conversation[-n:] if self._fallback_conversation else []

    # ── Active files ──────────────────────────────────────────────

    def push_file(self, file_path: str) -> None:
        if self._redis:
            key = KEY_ACTIVE_FILES.format(prefix=KEY_PREFIX, sid=self.session_id)
            self._redis.lpush(key, file_path)
            self._redis.ltrim(key, 0, 29)
        else:
            if file_path not in self._fallback_files:
                self._fallback_files.append(file_path)
            if len(self._fallback_files) > 30:
                self._fallback_files = self._fallback_files[-30:]

    def get_files(self, n: int = 10) -> list[str]:
        if self._redis:
            key = KEY_ACTIVE_FILES.format(prefix=KEY_PREFIX, sid=self.session_id)
            items = self._redis.lrange(key, 0, n - 1)
            return list(reversed(items))
        return self._fallback_files[-n:] if self._fallback_files else []

    # ── Tool outputs ──────────────────────────────────────────────

    def push_output(self, output: dict[str, Any]) -> None:
        if self._redis:
            key = KEY_TOOL_OUTPUTS.format(prefix=KEY_PREFIX, sid=self.session_id)
            self._redis.lpush(key, json.dumps(output, ensure_ascii=False))
            self._redis.ltrim(key, 0, 49)
        else:
            self._fallback_outputs.append(output)
            if len(self._fallback_outputs) > 50:
                self._fallback_outputs.pop(0)

    def get_outputs(self, n: int = 10) -> list[dict[str, Any]]:
        if self._redis:
            key = KEY_TOOL_OUTPUTS.format(prefix=KEY_PREFIX, sid=self.session_id)
            items = self._redis.lrange(key, 0, n - 1)
            return [json.loads(item) for item in reversed(items)]
        return self._fallback_outputs[-n:] if self._fallback_outputs else []

    # ── Metadata ──────────────────────────────────────────────────

    def set_metadata(self, key: str, value: str) -> None:
        if self._redis:
            rkey = KEY_METADATA.format(prefix=KEY_PREFIX, sid=self.session_id)
            self._redis.hset(rkey, key, value)
        else:
            self._fallback_metadata[key] = value

    def get_metadata(self, key: str) -> str | None:
        if self._redis:
            rkey = KEY_METADATA.format(prefix=KEY_PREFIX, sid=self.session_id)
            val = self._redis.hget(rkey, key)
            return val if val else None
        return self._fallback_metadata.get(key)

    def get_all_metadata(self) -> dict[str, str]:
        if self._redis:
            rkey = KEY_METADATA.format(prefix=KEY_PREFIX, sid=self.session_id)
            return self._redis.hgetall(rkey)
        return dict(self._fallback_metadata)

    # ── Retrieval cache ───────────────────────────────────────────

    def cache_retrieval(self, query: str, results: list[dict[str, Any]], ttl: int = 300) -> None:
        """Cache retrieval results to avoid recomputation (5 min TTL)."""
        if self._redis:
            import hashlib
            query_hash = hashlib.md5(query.encode()).hexdigest()[:12]
            key = KEY_RETRIEVAL_CACHE.format(prefix=KEY_PREFIX, sid=self.session_id, query_hash=query_hash)
            self._redis.setex(key, ttl, json.dumps(results, ensure_ascii=False))

    def get_cached_retrieval(self, query: str) -> list[dict[str, Any]] | None:
        if self._redis:
            import hashlib
            query_hash = hashlib.md5(query.encode()).hexdigest()[:12]
            key = KEY_RETRIEVAL_CACHE.format(prefix=KEY_PREFIX, sid=self.session_id, query_hash=query_hash)
            cached = self._redis.get(key)
            if cached:
                return json.loads(cached)
        return None

    # ── Lifecycle ─────────────────────────────────────────────────

    def clear_session(self) -> None:
        """Remove all keys for this session."""
        if self._redis:
            pattern = f"{KEY_PREFIX}:{self.session_id}:*"
            keys = list(self._redis.scan_iter(match=pattern))
            if keys:
                self._redis.delete(*keys)
                logger.info("Cleared %d Redis keys for session %s", len(keys), self.session_id)
        else:
            self._fallback_conversation.clear()
            self._fallback_files.clear()
            self._fallback_outputs.clear()
            self._fallback_metadata.clear()

    def close(self) -> None:
        if self._redis:
            try:
                self._redis.close()
            except Exception:
                pass
