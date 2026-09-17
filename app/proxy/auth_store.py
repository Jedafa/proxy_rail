import threading

from .. import db, security


class AuthStore:
    """Хранилище пользователей прокси ноды (SQLite + in-memory кэш)."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._users: dict[str, tuple[str, bool]] = {}  # username -> (pass_hash, active)
        self._lock = threading.Lock()
        self.reload()

    def reload(self) -> None:
        rows = db.query(self.db_path, "SELECT username, pass_hash, active FROM users")
        with self._lock:
            self._users = {r["username"]: (r["pass_hash"], bool(r["active"])) for r in rows}

    def check(self, username: str, password: str) -> bool:
        with self._lock:
            item = self._users.get(username)
        if not item:
            return False
        stored, active = item
        return active and security.verify_password(password, stored)

    def has_users(self) -> bool:
        with self._lock:
            return len(self._users) > 0

    def count(self) -> int:
        with self._lock:
            return len(self._users)

    def list(self) -> list[dict]:
        return db.query(
            self.db_path,
            "SELECT username, active, note, created_at FROM users ORDER BY created_at DESC",
        )
