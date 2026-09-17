"""Control API прокси-ноды (порт $PORT).

Защита: заголовок X-Node-Token. Через этот API core управляет пользователями
прокси и читает статистику. /health — публичный, только факт живости.
"""

import asyncio
import logging
import secrets as pysecrets
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from . import config, db, security
from .proxy.auth_store import AuthStore
from .proxy.server import start_servers
from .proxy.stats import Stats

log = logging.getLogger("node")

auth_store: AuthStore | None = None
stats = Stats()


class UserIn(BaseModel):
    username: str
    password: str
    note: str = ""


@asynccontextmanager
async def lifespan(app: FastAPI):
    global auth_store
    db.init_node(config.NODE_DB)
    auth_store = AuthStore(config.NODE_DB)
    if not config.NODE_TOKEN:
        log.warning("NODE_TOKEN пуст — API закрыт для всех (401). Задайте NODE_TOKEN!")
    servers = await start_servers(auth_store, stats)
    reg_task = None
    if config.CORE_URL and config.REG_TOKEN:
        reg_task = asyncio.create_task(_register_loop())
    log.info("node запущена: role=node, control_api=:%s, http=:%s, socks=:%s",
             config.PORT, config.HTTP_PROXY_PORT, config.SOCKS_PORT)
    yield
    if reg_task:
        reg_task.cancel()
    for s in servers:
        s.close()


app = FastAPI(title="proxy_rail node", version=config.APP_VERSION,
              lifespan=lifespan, docs_url=None, redoc_url=None)


def _check(request: Request) -> None:
    provided = request.headers.get("X-Node-Token", "")
    if not security.check_token(provided, config.NODE_TOKEN):
        raise HTTPException(status_code=401, detail="invalid node token")


@app.get("/health")
async def health():
    return {"ok": True, "role": "node", "version": config.APP_VERSION}


@app.get("/api/ping")
async def ping(request: Request):
    _check(request)
    return {"ok": True, "version": config.APP_VERSION,
            "uptime_s": int(time.time() - stats.start), "users": auth_store.count()}


@app.get("/api/users")
async def list_users(request: Request):
    _check(request)
    return {"users": auth_store.list()}


@app.post("/api/users")
async def upsert_user(data: UserIn, request: Request):
    _check(request)
    username = data.username.strip()
    if not username or not data.password:
        raise HTTPException(400, "username and password are required")
    db.execute(
        config.NODE_DB,
        "INSERT INTO users(username, pass_hash, active, note) VALUES(?,?,1,?) "
        "ON CONFLICT(username) DO UPDATE SET pass_hash=excluded.pass_hash, active=1",
        (username, security.hash_password(data.password), data.note),
    )
    auth_store.reload()
    log.info("пользователь прокси сохранён: %s", username)
    return {"ok": True, "username": username}


@app.delete("/api/users/{username}")
async def delete_user(username: str, request: Request):
    _check(request)
    db.execute(config.NODE_DB, "DELETE FROM users WHERE username=?", (username,))
    auth_store.reload()
    log.info("пользователь прокси удалён: %s", username)
    return {"ok": True}


@app.get("/api/stats")
async def get_stats(request: Request):
    _check(request)
    snap = stats.snapshot()
    snap["users_count"] = auth_store.count()
    return snap


async def _register_loop():
    """Автоматическая регистрация ноды в core (раз в 5 минут)."""
    if not config.PUBLIC_CONTROL_URL:
        log.warning("CORE_URL задан, но PUBLIC_CONTROL_URL пуст — авто-регистрация пропущена")
        return
    import httpx

    payload = {
        "name": config.NODE_NAME or "node-" + pysecrets.token_hex(3),
        "control_url": config.PUBLIC_CONTROL_URL,
        "token": config.NODE_TOKEN,
        "reg_token": config.REG_TOKEN,
        "proxy_host": config.PUBLIC_PROXY_HOST,
        "proxy_http_port": config.PUBLIC_HTTP_PORT,
        "proxy_socks_port": config.PUBLIC_SOCKS_PORT,
    }
    async with httpx.AsyncClient(timeout=15) as cli:
        while True:
            try:
                r = await cli.post(config.CORE_URL.rstrip("/") + "/api/nodes/register", json=payload)
                if r.status_code == 200:
                    log.info("нода зарегистрирована в core: %s", payload["name"])
                else:
                    log.warning("регистрация не удалась: HTTP %s %s", r.status_code, r.text[:120])
            except Exception as exc:
                log.warning("ошибка регистрации: %s", exc)
            await asyncio.sleep(300)
