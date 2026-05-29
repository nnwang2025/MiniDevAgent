from __future__ import annotations


class AuthService:
    """A simple authentication service for demo purposes."""

    def __init__(self, user_store: dict[str, dict[str, str]]) -> None:
        self.user_store = user_store

    def login(self, username: str, password: str) -> bool:
        if not username or not password:
            return False
        user = self.user_store.get(username)
        return bool(user and user.get("password") == password)

    def register(self, username: str, password: str) -> dict[str, str]:
        if username in self.user_store:
            raise ValueError("User already exists")
        self.user_store[username] = {"password": password}
        return {"username": username}


def is_authenticated(user: dict[str, str] | None) -> bool:
    return bool(user and user.get("password"))
