"""Web: веб-админка (FastAPI + Jinja2) + внутренний API (регистрация нод, API для бота).

Это мастер-сервис: здесь единственная БД (nodes, creds). Бот работает тонким
клиентом через /api/bot/* с заголовком X-Bot-Token.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel

from . import config, db, security
from .core_service import (check_node, create_cred, delete_cred, delete_node, get_cred,
                           get_cred_by_username, get_node, get_node_by_name, link_set,
                           list_creds, list_nodes, node_request, sync_node, upsert_node)

log = logging.getLogger("web")

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
signer = URLSafeTimedSerializer(config.SESSION_SECRET, salt="proxy_rail-admin")
COOKIE = "pr_admin"


# --------------------------------------------------------------------- auth utils

def _is_admin(request: Request) -> bool:
    raw = request.cookies.get(COOKIE)
    if not raw:
        return False
    try:
        data = signer.loads(raw, max_age=7 * 24 * 3600)
        return data.get("a") is True
    except (BadSignature, SignatureExpired):
        return False


def _guard(request: Request):
    if not _is_admin(request):
        return RedirectResponse("/login", status_code=303)
    return None


def _redirect(path: str, msg: str = "", err: str = "") -> RedirectResponse:
    if msg:
        path += ("&" if "?" in path else "?") + "msg=" + quote(msg)
    if err:
        path += ("&" if "?" in path else "?") + "err=" + quote(err)
    return RedirectResponse(path, status_code=303)


def _ctx(request: Request, **kw) -> dict:
    kw.setdefault("is_admin", _is_admin(request))
    kw.setdefault("msg", "")
    kw.setdefault("err", "")
    return kw


# ------------------------------------------------------------------------ lifespan

@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_core(config.CORE_DB)
    tasks: list[asyncio.Task] = []
    if config.ROLE == "all":
        # Локальный режим "всё в одном": админка + бот + прокси-движок в одном процессе
        if config.BOT_TOKEN:
            async def _bot():
                await asyncio.sleep(2)  # даём uvicorn подняться, бот ходит в web по HTTP
                from .bot import start_bot
                await start_bot()
            tasks.append(asyncio.create_task(_bot()))
        from .node_api import stats as node_stats
        from .proxy.auth_store import AuthStore
        from .proxy.server import start_servers
        db.init_node(config.NODE_DB)
        app.state.auth_store = AuthStore(config.NODE_DB)
        app.state.proxy_servers = await start_servers(app.state.auth_store, node_stats)
    log.info("web запущен: role=%s, port=%s", config.ROLE, config.PORT)
    yield
    for t in tasks:
        t.cancel()
    for s in getattr(app.state, "proxy_servers", []):
        s.close()


app = FastAPI(title="proxy_rail web", version=config.APP_VERSION,
              lifespan=lifespan, docs_url=None, redoc_url=None)


# --------------------------------------------------------------------------- auth

@app.get("/login", response_class=None)
async def login_page(request: Request, msg: str = "", err: str = ""):
    if _is_admin(request):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", _ctx(request, msg=msg, err=err))


@app.post("/login")
async def login(request: Request, password: str = Form("")):
    if not password or not security.check_token(password, config.ADMIN_PASSWORD):
        return _redirect("/login", err="Неверный пароль")
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(COOKIE, signer.dumps({"a": True}), httponly=True,
                    samesite="lax", max_age=7 * 24 * 3600)
    return resp


@app.get("/logout")
async def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE)
    return resp


# ----------------------------------------------------------------------- dashboard

@app.get("/")
async def dashboard(request: Request, msg: str = "", err: str = ""):
    g = _guard(request)
    if g:
        return g
    nodes = list_nodes()
    creds = list_creds()
    online = sum(1 for n in nodes if n["status"] == "online")
    return templates.TemplateResponse(
        request, "dashboard.html",
        _ctx(request, nodes=nodes, creds_count=len(creds),
             nodes_count=len(nodes), online=online, msg=msg, err=err))


# --------------------------------------------------------------------------- nodes

@app.get("/nodes")
async def nodes_page(request: Request, msg: str = "", err: str = ""):
    g = _guard(request)
    if g:
        return g
    return templates.TemplateResponse(request, "nodes.html", _ctx(request, nodes=list_nodes(),
                                                                  msg=msg, err=err))


@app.post("/nodes")
async def nodes_add(request: Request, name: str = Form(""), control_url: str = Form(""),
                    token: str = Form(""), proxy_host: str = Form(""),
                    proxy_http_port: int = Form(0), proxy_socks_port: int = Form(0)):
    g = _guard(request)
    if g:
        return g
    name, control_url, token = name.strip(), control_url.strip(), token.strip()
    if not name or not control_url or not token:
        return _redirect("/nodes", err="Заполните имя, URL и токен ноды")
    upsert_node(name, control_url, token, proxy_host.strip(), proxy_http_port, proxy_socks_port)
    node = get_node_by_name(name)
    if node:
        await check_node(node)
    return _redirect("/nodes", msg=f"Нода «{name}» сохранена")


@app.post("/nodes/{node_id}/delete")
async def nodes_delete(request: Request, node_id: int):
    g = _guard(request)
    if g:
        return g
    delete_node(node_id)
    return _redirect("/nodes", msg="Нода и её прокси удалены")


@app.post("/nodes/{node_id}/check")
async def nodes_check(request: Request, node_id: int):
    g = _guard(request)
    if g:
        return g
    node = get_node(node_id)
    if node:
        ok = await check_node(node)
        return _redirect("/nodes", msg=f"{node['name']}: online" if ok
                         else f"{node['name']}: offline")
    return _redirect("/nodes", err="Нода не найдена")


@app.post("/nodes/{node_id}/sync")
async def nodes_sync(request: Request, node_id: int):
    g = _guard(request)
    if g:
        return g
    node = get_node(node_id)
    if not node:
        return _redirect("/nodes", err="Нода не найдена")
    ok = await sync_node(node)
    return _redirect("/nodes", msg=f"Синхронизировано {ok} прокси на «{node['name']}»")


@app.post("/nodes/check_all")
async def nodes_check_all(request: Request):
    g = _guard(request)
    if g:
        return g
    for n in list_nodes():
        await check_node(n)
    return _redirect("/nodes", msg="Проверка всех нод завершена")


# --------------------------------------------------------------------------- creds

@app.get("/creds")
async def creds_page(request: Request, node: int = 0, msg: str = "", err: str = ""):
    g = _guard(request)
    if g:
        return g
    return templates.TemplateResponse(
        request, "creds.html",
        _ctx(request, creds=list_creds(node or None), nodes=list_nodes(),
             cur_node=node, msg=msg, err=err))


@app.post("/creds")
async def creds_add(request: Request, node_id: int = Form(0), username: str = Form(""),
                    password: str = Form(""), label: str = Form("")):
    g = _guard(request)
    if g:
        return g
    node = get_node(node_id)
    if not node:
        return _redirect("/creds", err="Выберите ноду")
    u, p, err = await create_cred(node, username, password, label, None)
    if err:
        return _redirect("/creds", err=err)
    return _redirect("/creds", msg=f"Прокси создан: {u}")


@app.post("/creds/{cred_id}/delete")
async def creds_delete(request: Request, cred_id: int):
    g = _guard(request)
    if g:
        return g
    cred = get_cred(cred_id)
    if cred:
        await delete_cred(cred)
        return _redirect("/creds", msg=f"Прокси «{cred['username']}» удалён")
    return _redirect("/creds", err="Прокси не найден")


# --------------------------------------------------- внутренний API (для нод)

class RegisterIn(BaseModel):
    name: str
    control_url: str
    token: str
    reg_token: str = ""
    proxy_host: str = ""
    proxy_http_port: int = 0
    proxy_socks_port: int = 0


@app.post("/api/nodes/register")
async def api_register_node(data: RegisterIn):
    if not config.REG_TOKEN or not security.check_token(data.reg_token, config.REG_TOKEN):
        raise HTTPException(401, "invalid reg token")
    upsert_node(data.name, data.control_url, data.token,
                data.proxy_host, data.proxy_http_port, data.proxy_socks_port)
    log.info("нода зарегистрирована автоматически: %s", data.name)
    return {"ok": True}


# -------------------------------------------------- внутренний API для бота

def _bot_ok(request: Request) -> None:
    provided = request.headers.get("X-Bot-Token", "")
    if not config.BOT_API_TOKEN or not security.check_token(provided, config.BOT_API_TOKEN):
        raise HTTPException(401, "invalid bot token")


class BotNodeIn(BaseModel):
    name: str
    control_url: str
    token: str
    proxy_host: str = ""
    proxy_http_port: int = 0
    proxy_socks_port: int = 0


class BotCredIn(BaseModel):
    node_name: str
    label: str = ""
    username: str = ""
    password: str = ""


@app.get("/api/bot/nodes")
async def api_bot_nodes(request: Request):
    _bot_ok(request)
    return {"nodes": list_nodes()}


@app.post("/api/bot/nodes")
async def api_bot_nodes_add(request: Request, data: BotNodeIn):
    _bot_ok(request)
    if not data.name.strip() or not data.control_url.strip() or not data.token.strip():
        raise HTTPException(400, "name, control_url and token are required")
    upsert_node(data.name.strip(), data.control_url.strip(), data.token.strip(),
                data.proxy_host.strip(), data.proxy_http_port, data.proxy_socks_port)
    node = get_node_by_name(data.name.strip())
    online = await check_node(node) if node else False
    return {"ok": True, "online": online}


@app.post("/api/bot/nodes/check_all")
async def api_bot_nodes_check_all(request: Request):
    _bot_ok(request)
    res = []
    for n in list_nodes():
        ok = await check_node(n)
        res.append({"name": n["name"], "ok": ok, "proxy_host": n["proxy_host"],
                    "http": n["proxy_http_port"], "socks": n["proxy_socks_port"]})
    return {"nodes": res}


@app.post("/api/bot/nodes/{name}/sync")
async def api_bot_node_sync(name: str, request: Request):
    _bot_ok(request)
    node = get_node_by_name(name)
    if not node:
        raise HTTPException(404, "node not found")
    return {"synced": await sync_node(node)}


@app.post("/api/bot/creds")
async def api_bot_creds_add(request: Request, data: BotCredIn):
    _bot_ok(request)
    node = get_node_by_name(data.node_name)
    if not node:
        raise HTTPException(404, "node not found")
    u, p, err = await create_cred(node, data.username, data.password, data.label, None)
    if err:
        raise HTTPException(502, err)
    log.info("бот создал прокси %s на ноде %s", u, node["name"])
    return {"username": u, "password": p, "links": link_set(node, u, p)}


@app.get("/api/bot/creds")
async def api_bot_creds(request: Request):
    _bot_ok(request)
    return {"creds": list_creds()}


@app.delete("/api/bot/creds/{username}")
async def api_bot_creds_delete(username: str, request: Request):
    _bot_ok(request)
    cred = get_cred_by_username(username)
    if not cred:
        raise HTTPException(404, "cred not found")
    await delete_cred(cred)
    return {"ok": True}


@app.get("/api/bot/stats")
async def api_bot_stats(request: Request):
    _bot_ok(request)
    nodes_res: list[dict] = []
    total: dict[str, list[int]] = {}
    for n in list_nodes():
        try:
            r = await node_request(n, "GET", "/api/stats", timeout=8)
            if r.status_code != 200:
                nodes_res.append({"name": n["name"], "ok": False,
                                  "error": f"HTTP {r.status_code}"})
                continue
            data = r.json()
            rx = tx = 0
            for uname, v in (data.get("users") or {}).items():
                rx += v.get("rx", 0)
                tx += v.get("tx", 0)
                acc = total.setdefault(uname, [0, 0])
                acc[0] += v.get("rx", 0)
                acc[1] += v.get("tx", 0)
            nodes_res.append({"name": n["name"], "ok": True, "rx": rx, "tx": tx,
                              "conns": data.get("total_conns", 0)})
        except Exception as exc:
            nodes_res.append({"name": n["name"], "ok": False, "error": str(exc)[:60]})
    top = [{"user": u, "rx": v[0], "tx": v[1]}
           for u, v in sorted(total.items(), key=lambda kv: -(kv[1][0] + kv[1][1]))[:10]]
    return {"nodes": nodes_res, "top": top}
