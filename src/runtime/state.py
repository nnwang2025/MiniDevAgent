from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SessionState:
    project_path: str
    session_id: str
    hot_memory: Any
    cold_memory: Any
    code_index: Any
    metadata: dict[str, Any] = field(default_factory=dict)
    agent_trace: list[dict[str, Any]] = field(default_factory=list)

    def record_trace(self, step: str, detail: dict[str, Any]) -> None:
        self.agent_trace.append({"step": step, "detail": detail})

    def update_metadata(self, key: str, value: Any) -> None:
        self.metadata[key] = value
