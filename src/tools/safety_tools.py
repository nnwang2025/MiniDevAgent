from __future__ import annotations
from pathlib import Path


class SafetyError(Exception):
    pass


def resolve_safe_path(project_path: str, file_path: str) -> Path:
    base_root = Path(project_path).resolve()
    candidate = Path(file_path)
    if not candidate.is_absolute():
        candidate = (base_root / candidate).resolve()
    else:
        candidate = candidate.resolve()
    if base_root != candidate and base_root not in candidate.parents:
        raise SafetyError("Access denied: path is outside the project root.")
    return candidate


def normalize_project_path(project_path: str) -> str:
    return str(Path(project_path).resolve())
