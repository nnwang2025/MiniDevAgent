from __future__ import annotations

from collections import Counter
from datetime import datetime
import re
from typing import Any


MAX_GAP_SECONDS = 60 * 60 * 6


def build_episodic_chunks(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(records, key=lambda item: item.get("timestamp") or "")
    chunks: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []

    for record in ordered:
        if not current:
            current = [record]
            continue
        if _should_split(current[-1], record):
            chunks.append(_make_chunk(current, len(chunks) + 1))
            current = [record]
        else:
            current.append(record)
    if current:
        chunks.append(_make_chunk(current, len(chunks) + 1))
    return chunks


def _should_split(previous: dict[str, Any], current: dict[str, Any]) -> bool:
    previous_time = _parse_time(previous.get("timestamp", ""))
    current_time = _parse_time(current.get("timestamp", ""))
    if previous_time and current_time and abs((current_time - previous_time).total_seconds()) > MAX_GAP_SECONDS:
        return True
    if previous.get("kind") and current.get("kind") and previous.get("kind") != current.get("kind"):
        return True
    previous_topics = set(_topic_hints(previous.get("text", "")))
    current_topics = set(_topic_hints(current.get("text", "")))
    return bool(previous_topics and current_topics and previous_topics.isdisjoint(current_topics))


def _make_chunk(records: list[dict[str, Any]], number: int) -> dict[str, Any]:
    source_types = sorted({record.get("kind", "record") for record in records if record.get("kind")})
    participants = sorted({participant for record in records for participant in _participants(record)})
    entities = sorted({entity for record in records for entity in record.get("entities", [])} | {entity for record in records for entity in _entities(record.get("text", ""))})
    topics = _top_items(topic for record in records for topic in _topic_hints(record.get("text", "")))
    return {
        "record_id": f"chunk-{number}",
        "time_start": min((record.get("timestamp", "") for record in records if record.get("timestamp")), default=""),
        "time_end": max((record.get("timestamp", "") for record in records if record.get("timestamp")), default=""),
        "source_types": source_types,
        "participants": participants,
        "topic_hints": topics,
        "entities": entities,
        "summary": _summary(records, topics),
        "raw_records": records,
    }


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value[:19], fmt)
        except ValueError:
            continue
    return None


def _participants(record: dict[str, Any]) -> list[str]:
    participants = record.get("participants", [])
    if isinstance(participants, list):
        return [str(item) for item in participants]
    return []


def _entities(text: str) -> list[str]:
    return re.findall(r"\b[A-Z][A-Za-z0-9_]+\b|\b[A-Za-z_]+Agent\b|\b[A-Za-z_]+Skill\b", text)


def _topic_hints(text: str) -> list[str]:
    topics: list[str] = []
    rules = {
        "project_direction": ["方向", "定位", "简历", "工程化", "重构"],
        "runtime": ["Runtime", "运行时", "SkillRouter", "ContextManager", "PlannerAgent", "AnswerAgent"],
        "memory": ["memory", "记忆", "chunk", "检索", "cold memory"],
        "bug": ["bug", "报错", "traceback", "error", "异常"],
        "api": ["FastAPI", "/ask", "API", "uvicorn"],
    }
    lower = text.lower()
    for topic, keywords in rules.items():
        if any(keyword.lower() in lower or keyword in text for keyword in keywords):
            topics.append(topic)
    topics.extend(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text)[:5])
    return _top_items(topics)


def _summary(records: list[dict[str, Any]], topics: list[str]) -> str:
    pieces = [record.get("text", "").strip() for record in records if record.get("text")]
    joined = " ".join(pieces)
    prefix = f"主题 {', '.join(topics[:3])}：" if topics else ""
    return prefix + (joined[:300] + ("..." if len(joined) > 300 else ""))


def _top_items(values: Any, limit: int = 8) -> list[str]:
    counter = Counter(str(value) for value in values if str(value).strip())
    return [item for item, _ in counter.most_common(limit)]
