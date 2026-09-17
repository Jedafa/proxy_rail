import time


class Stats:
    """Счётчики трафика и соединений (in-memory, сбрасываются при рестарте)."""

    def __init__(self):
        self.start = time.time()
        self.users: dict[str, dict[str, int]] = {}
        self.total_conns = 0

    def conn_open(self, user: str | None) -> None:
        self.total_conns += 1
        u = self.users.setdefault(user or "anonymous", {"rx": 0, "tx": 0, "conns": 0})
        u["conns"] += 1

    def add(self, user: str | None, rx: int = 0, tx: int = 0) -> None:
        u = self.users.setdefault(user or "anonymous", {"rx": 0, "tx": 0, "conns": 0})
        u["rx"] += rx
        u["tx"] += tx

    def snapshot(self) -> dict:
        return {
            "uptime_s": int(time.time() - self.start),
            "total_conns": self.total_conns,
            "users": {k: dict(v) for k, v in self.users.items()},
        }
