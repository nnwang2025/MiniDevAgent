from __future__ import annotations


class DatabaseConnection:
    """A mock database connection for demo project analysis."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self.connected = False

    def connect(self) -> str:
        self.connected = True
        return f"Connected to {self.dsn}"

    def disconnect(self) -> str:
        self.connected = False
        return "Disconnected"


def get_default_connection() -> DatabaseConnection:
    return DatabaseConnection(dsn="sqlite:///:memory:")
