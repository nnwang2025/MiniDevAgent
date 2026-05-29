from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class HotMemory:
    recent_conversation: list[str] = field(default_factory=list)
    active_files: list[str] = field(default_factory=list)
    tool_outputs: list[dict[str, Any]] = field(default_factory=list)

    def add_message(self, message: str) -> None:
        self.recent_conversation.append(message)
        if len(self.recent_conversation) > 20:
            self.recent_conversation.pop(0)

    def add_file(self, file_path: str) -> None:
        if file_path not in self.active_files:
            self.active_files.append(file_path)
        if len(self.active_files) > 30:
            self.active_files = self.active_files[-30:]

    def add_output(self, output: dict[str, Any]) -> None:
        self.tool_outputs.append(output)
        if len(self.tool_outputs) > 50:
            self.tool_outputs.pop(0)
