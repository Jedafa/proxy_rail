import os
import sqlite3


def connect(path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    con = sqlite3.connect(path, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def init_core(path: str) -> None:
    con = connect(path)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS nodes(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            kind TEXT DEFAULT 'proxy',
            control_url TEXT NOT NULL,
            token TEXT NOT NULL,
            proxy_host TEXT DEFAULT '',
            proxy_http_port INTEGER DEFAULT 0,
            proxy_socks_port INTEGER DEFAULT 0,
            status TEXT DEFAULT 'unknown',
            last_check TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS creds(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            node_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            password TEXT NOT NULL,
            label TEXT DEFAULT '',
            tg_user_id INTEGER,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(node_id, username)
        );
        """
    )
    # миграция для БД, созданных до появления kind ('proxy' | 'tgweb')
    cols = {r["name"] for r in query(path, "PRAGMA table_info(nodes)")}
    if "kind" not in cols:
        execute(path, "ALTER TABLE nodes ADD COLUMN kind TEXT DEFAULT 'proxy'")
    con.commit()
    con.close()


def init_node(path: str) -> None:
    con = connect(path)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS users(
            username TEXT PRIMARY KEY,
            pass_hash TEXT NOT NULL,
            active INTEGER DEFAULT 1,
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        );
        """
    )
    con.commit()
    con.close()


def query(path: str, sql: str, params: tuple = ()) -> list[dict]:
    con = connect(path)
    try:
        cur = con.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]
        con.commit()
        return rows
    finally:
        con.close()


def execute(path: str, sql: str, params: tuple = ()) -> int:
    con = connect(path)
    try:
        cur = con.execute(sql, params)
        con.commit()
        return cur.lastrowid
    finally:
        con.close()
