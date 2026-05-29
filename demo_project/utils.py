from __future__ import annotations


def normalize_username(username: str) -> str:
    return username.strip().lower()


def format_message(message: str) -> str:
    return f"[demo] {message}"
