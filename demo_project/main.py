from __future__ import annotations
from .auth import AuthService, is_authenticated
from .database import get_default_connection
from .utils import format_message, normalize_username


def bootstrap() -> str:
    connection = get_default_connection()
    status = connection.connect()
    auth = AuthService(user_store={"alice": {"password": "secret"}})
    user = {"username": normalize_username("Alice"), "password": "secret"}
    authorized = auth.login(user["username"], user["password"])
    if authorized and is_authenticated(user):
        return format_message(status + " and user authenticated.")
    return format_message("Authentication failed.")


if __name__ == "__main__":
    print(bootstrap())
