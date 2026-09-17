"""Сервисный слой core: работа с нодами, прокси-доступами, ссылками."""

from urllib.parse import quote

from . import config, db, security


# ------------------------------------------------------------- узлы (ноды)

def list_nodes() -> list[dict]:
    return db.query(config.CORE_DB, "SELECT * FROM nodes ORDER BY id")


def get_node(node_id: int) -> dict | None:
    rows = db.query(config.CORE_DB, "SELECT * FROM nodes WHERE id=?", (node_id,))
    return rows[0] if rows else None


def get_node_by_name(name: str) -> dict | None:
    rows = db.query(config.CORE_DB, "SELECT * FROM nodes WHERE name=?", (name,))
    return rows[0] if rows else None


def upsert_node(name: str, control_url: str, token: str, proxy_host: str,
                proxy_http_port: int, proxy_socks_port: int,
                kind: str = "proxy") -> None:
    db.execute(
        config.CORE_DB,
        "INSERT INTO nodes(name, kind, control_url, token, proxy_host, proxy_http_port, proxy_socks_port) "
        "VALUES(?,?,?,?,?,?,?) "
        "ON CONFLICT(name) DO UPDATE SET kind=excluded.kind, control_url=excluded.control_url, "
        "token=excluded.token, proxy_host=excluded.proxy_host, proxy_http_port=excluded.proxy_http_port, "
        "proxy_socks_port=excluded.proxy_socks_port",
        (name, kind, control_url.rstrip("/"), token, proxy_host, proxy_http_port, proxy_socks_port),
    )


def delete_node(node_id: int) -> None:
    db.execute(config.CORE_DB, "DELETE FROM creds WHERE node_id=?", (node_id,))
    db.execute(config.CORE_DB, "DELETE FROM nodes WHERE id=?", (node_id,))


# ------------------------------------------------------------- HTTP-клиент к ноде

async def node_request(node: dict, method: str, path: str, json=None, timeout: float = 10.0):
    import httpx

    url = node["control_url"].rstrip("/") + path
    headers = {"X-Node-Token": node["token"]}
    async with httpx.AsyncClient(timeout=timeout) as cli:
        return await cli.request(method, url, headers=headers, json=json)


async def check_node(node: dict) -> bool:
    """Пинг ноды, обновление статуса в БД. Возвращает True если онлайн."""
    if node.get("kind") == "tgweb":
        # У WEB-нод нет control API: считаем «онлайн», если задан host и секрет.
        ok = bool(node.get("proxy_host") and node.get("token"))
        db.execute(
            config.CORE_DB,
            "UPDATE nodes SET status=?, last_check=datetime('now') WHERE id=?",
            ("online" if ok else "offline", node["id"]),
        )
        return ok
    try:
        r = await node_request(node, "GET", "/api/ping", timeout=8)
        ok = r.status_code == 200
    except Exception:
        ok = False
    db.execute(
        config.CORE_DB,
        "UPDATE nodes SET status=?, last_check=datetime('now') WHERE id=?",
        ("online" if ok else "offline", node["id"]),
    )
    return ok


# ------------------------------------------------------------- прокси-доступы

def list_creds(node_id: int | None = None) -> list[dict]:
    sql = (
        "SELECT c.*, n.name AS node_name, n.proxy_host, n.proxy_http_port, n.proxy_socks_port "
        "FROM creds c JOIN nodes n ON n.id=c.node_id"
    )
    if node_id:
        return db.query(config.CORE_DB, sql + " WHERE c.node_id=? ORDER BY c.id DESC", (node_id,))
    return db.query(config.CORE_DB, sql + " ORDER BY c.id DESC")


def get_cred(cred_id: int) -> dict | None:
    rows = db.query(config.CORE_DB, "SELECT * FROM creds WHERE id=?", (cred_id,))
    return rows[0] if rows else None


def get_cred_by_username(username: str) -> dict | None:
    rows = db.query(config.CORE_DB, "SELECT * FROM creds WHERE username=?", (username,))
    return rows[0] if rows else None


async def create_cred(node: dict, username: str, password: str, label: str, tg_user_id: int | None):
    """Создаёт пользователя на ноде и сохраняет доступ в БД core."""
    username = (username or "").strip() or security.gen_username()
    password = (password or "").strip() or security.gen_password()
    try:
        r = await node_request(node, "POST", "/api/users",
                               json={"username": username, "password": password, "note": label or ""})
        if r.status_code != 200:
            return None, None, f"нода недоступна (HTTP {r.status_code})"
    except Exception as exc:
        return None, None, f"нода недоступна: {str(exc)[:80]}"
    db.execute(
        config.CORE_DB,
        "INSERT INTO creds(node_id, username, password, label, tg_user_id) VALUES(?,?,?,?,?) "
        "ON CONFLICT(node_id, username) DO UPDATE SET password=excluded.password, label=excluded.label",
        (node["id"], username, password, label or "", tg_user_id),
    )
    return username, password, ""


async def delete_cred(cred: dict) -> None:
    node = get_node(cred["node_id"])
    if node:
        try:
            await node_request(node, "DELETE", "/api/users/" + cred["username"])
        except Exception:
            pass
    db.execute(config.CORE_DB, "DELETE FROM creds WHERE id=?", (cred["id"],))


async def sync_node(node: dict) -> int:
    """Перезаливает все доступы core на ноду (после переезда/сброса ноды)."""
    creds = db.query(config.CORE_DB, "SELECT * FROM creds WHERE node_id=?", (node["id"],))
    ok = 0
    for c in creds:
        try:
            r = await node_request(node, "POST", "/api/users",
                                   json={"username": c["username"], "password": c["password"],
                                         "note": c["label"] or ""})
            if r.status_code == 200:
                ok += 1
        except Exception:
            pass
    return ok


# ------------------------------------------------------------- форматирование

def link_set(node: dict, username: str, password: str) -> dict:
    """Набор готовых ссылок для клиента (web-админка, бот)."""
    host = node["proxy_host"] or "NODE_HOST_NOT_SET"
    hp = node["proxy_http_port"] or 0
    sp = node["proxy_socks_port"] or 0
    return {
        "http": f"http://{username}:{password}@{host}:{hp}",
        "socks": f"socks5://{username}:{password}@{host}:{sp}",
        "tg": f"https://t.me/socks?server={host}&port={sp}&user={username}&pass={password}",
    }


def _tgweb_client_secret(secret_hex: str, has_base_path: bool) -> str:
    """Секрет для ссылки WEB-прокси.

    Без base path — чистый hex, как у MTProxy. С base path — помеченная форма:
    0x70 || secret -> unpadded base64url (см. README tproxy-server, раздел Base Path).
    """
    s = (secret_hex or "").strip().lower()
    if not has_base_path:
        return s
    import base64
    raw = bytes.fromhex(s) if all(c in "0123456789abcdef" for c in s) and len(s) % 2 == 0 else s.encode()
    return base64.urlsafe_b64encode(b"\x70" + raw).decode().rstrip("=")


def tgweb_links(node: dict) -> dict:
    """Ссылки для WEB-прокси Telegram (новый тип из Telegram Desktop 7.1+, авг. 2026).

    Порт всегда 443 (HTTPS), он в ссылку не входит. server может содержать base path,
    тогда он процен-encoded, а секрет — помеченная base64url-форма.
    """
    server = (node["proxy_host"] or "WEB_HOST_NOT_SET").strip()
    secret = _tgweb_client_secret(node["token"], "/" in server)
    server_enc = quote(server, safe="")
    return {
        "tme": f"https://t.me/webproxy?server={server_enc}&secret={secret}",
        "tg": f"tg://webproxy?server={server_enc}&secret={secret}",
        "server": server,
        "secret": secret,
    }


def fmt_bytes(n) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{int(n)} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"
