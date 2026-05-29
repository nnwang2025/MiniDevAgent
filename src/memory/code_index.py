from __future__ import annotations
from dataclasses import dataclass
from typing import Any


@dataclass
class CodeIndex:
    root: str
    files: list[dict[str, Any]]

    def file_names(self) -> list[str]:
        return [file_entry["path"] for file_entry in self.files]

    def find_by_keyword(self, keyword: str) -> list[dict[str, Any]]:
        return [entry for entry in self.files if keyword.lower() in entry["path"].lower() or keyword.lower() in entry["summary"].lower()]
