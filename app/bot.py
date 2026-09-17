"""Telegram-бот proxy_rail (aiogram 3, long polling).

Бот — тонкий клиент: все данные берёт из веб-панели по HTTP
(WEB_URL + заголовок X-Bot-Token: BOT_API_TOKEN). Своей БД у бота нет.
"""

import html
import logging
from urllib.parse import quote

import httpx
from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import LinkPreviewOptions, Message

from . import config

log = logging.getLogger("bot")
router = Router()

NO_ACCESS = "⛔ Нет доступа. Ваш Telegram ID: <code>{id}</code> — добавьте его в ADMIN_TG_IDS"

HELP_TEXT = (
    "🛡 <b>proxy_rail</b> — управление прокси\n\n"
    "/nodes — серверы и статус\n"
    "/addnode — добавить сервер (ноду)\n"
    "/newproxy &lt;нода&gt; [метка] — создать прокси\n"
    "/proxies — список прокси\n"
    "/delproxy &lt;логин&gt; — удалить прокси\n"
    "/addtgweb — добавить WEB-прокси для Telegram\n"
    "/tgweb — список WEB-прокси и ссылки\n"
    "/delnode &lt;имя&gt; — удалить ноду\n"
    "/sync &lt;нода&gt; — перезалить прокси на ноду\n"
    "/stats — трафик по нодам\n"
    "/help — эта справка"
)


def _admin(m: Message) -> bool:
    return bool(m.from_user and m.from_user.id in config.ADMIN_TG_IDS)


def _deny(m: Message):
    return m.answer(NO_ACCESS.format(id=m.from_user.id if m.from_user else "?"),
                    parse_mode="HTML")


async def _call(m: Message, method: str, path: str, **kw):
    """HTTP-запрос к веб-панели с обработкой ошибок. None — при фатальной ошибке."""
    try:
        async with httpx.AsyncClient(timeout=20,
                                     headers={"X-Bot-Token": config.BOT_API_TOKEN},
                                     base_url=config.WEB_URL) as cli:
            r = await cli.request(method, path, **kw)
    except httpx.HTTPError as exc:
        log.warning("web недоступна: %s", exc)
        await m.answer("⚠️ Веб-панель недоступна (проверьте WEB_URL у бота)")
        return None
    if r.status_code == 401:
        await m.answer("⚠️ Веб-панель отклонила токен бота "
                       "(BOT_API_TOKEN не совпадает с web)")
        return None
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail")
        except Exception:
            detail = None
        await m.answer(f"❌ Ошибка: {html.escape(str(detail or r.text[:120]))}")
        return None
    return r


# ------------------------------------------------------------------------ команды

@router.message(CommandStart())
async def cmd_start(m: Message):
    if not _admin(m):
        return await _deny(m)
    await m.answer(HELP_TEXT, parse_mode="HTML")


@router.message(Command("help"))
async def cmd_help(m: Message):
    if not _admin(m):
        return await _deny(m)
    await m.answer(HELP_TEXT, parse_mode="HTML")


@router.message(Command("addnode"))
async def cmd_addnode(m: Message):
    if not _admin(m):
        return await _deny(m)
    parts = (m.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await m.answer(
            "Формат:\n<code>/addnode Имя|URL|Токен|Host|HTTPпорт|SOCKSпорт</code>\n\n"
            "Пример:\n<code>/addnode node1|https://node1-production.up.railway.app|"
            "secret-token|xxx.up.rlwy.net|31234|45678</code>",
            parse_mode="HTML")
        return
    items = [s.strip() for s in parts[1].split("|")]
    if len(items) != 6:
        await m.answer("Нужно ровно 6 значений через <code>|</code>: "
                       "Имя|URL|Токен|Host|HTTPпорт|SOCKSпорт", parse_mode="HTML")
        return
    name, url, token, host, hport, sport = items
    try:
        hport_i, sport_i = int(hport), int(sport)
    except ValueError:
        await m.answer("Порты должны быть числами")
        return
    r = await _call(m, "POST", "/api/bot/nodes", json={
        "name": name, "control_url": url, "token": token, "proxy_host": host,
        "proxy_http_port": hport_i, "proxy_socks_port": sport_i})
    if r is None:
        return
    online = r.json().get("online", False)
    if online:
        await m.answer(f"🟢 Нода <b>{html.escape(name)}</b> добавлена, соединение в порядке",
                       parse_mode="HTML")
    else:
        await m.answer(f"🟠 Нода <b>{html.escape(name)}</b> добавлена, но не отвечает — "
                       "проверьте URL и токен (/nodes)", parse_mode="HTML")


@router.message(Command("addtgweb"))
async def cmd_addtgweb(m: Message):
    """WEB-прокси — новый тип прокси Telegram (Telegram Desktop 7.1+, авг. 2026).

    MTProxy-трафик, упакованный в HTTPS/WebSocket. Нужен свой домен и сервер
    с tproxy-server (см. tgweb/ в репозитории). Порт всегда 443.
    """
    if not _admin(m):
        return await _deny(m)
    parts = (m.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await m.answer(
            "Формат:\n<code>/addtgweb Имя|Домен|Секрет</code>\n\n"
            "Домен — с HTTPS на 443 (порт не указывается), секрет — 32 hex-символа\n"
            "(<code>openssl rand -hex 16</code>). Деплой сервера: см. tgweb/README.md\n\n"
            "Пример:\n<code>/addtgweb web1|tg1.example.com|000102030405060708090a0b0c0d0e0f</code>",
            parse_mode="HTML")
        return
    items = [s.strip() for s in parts[1].split("|")]
    if len(items) != 3:
        await m.answer("Нужно ровно 3 значения через <code>|</code>: Имя|Домен|Секрет",
                       parse_mode="HTML")
        return
    name, host, secret = items
    r = await _call(m, "POST", "/api/bot/nodes", json={
        "name": name, "control_url": "", "token": secret,
        "proxy_host": host, "kind": "tgweb"})
    if r is None:
        return
    ok = r.json().get("online", False)
    if ok:
        await m.answer(f"🟢 WEB-прокси <b>{html.escape(name)}</b> добавлен. "
                       "Ссылки: /tgweb", parse_mode="HTML")
    else:
        await m.answer(f"🟠 WEB-прокси <b>{html.escape(name)}</b> сохранён, но данные "
                       "неполные — проверьте домен и секрет (/tgweb)", parse_mode="HTML")


@router.message(Command("tgweb"))
async def cmd_tgweb(m: Message):
    if not _admin(m):
        return await _deny(m)
    r = await _call(m, "GET", "/api/bot/tgweb")
    if r is None:
        return
    rows = r.json().get("nodes", [])
    if not rows:
        await m.answer("WEB-прокси пока нет. Добавьте: /addtgweb\n\n"
                       "Это новый тип прокси Telegram (MTProxy внутри HTTPS) — "
                       "для него нужен свой домен и сервер, см. tgweb/README.md в репо")
        return
    for x in rows:
        links = x.get("links", {})
        await m.answer(
            f"✈️ <b>{html.escape(x['name'])}</b> (WEB-прокси)\n"
            f"Сервер: <code>{html.escape(links.get('server', '—'))}</code>\n\n"
            f"Открыть в Telegram:\n{html.escape(links.get('tme', '—'))}\n\n"
            f"Альтернативная:\n<code>{html.escape(links.get('tg', '—'))}</code>\n\n"
            f"Секрет для ручного ввода: <code>{html.escape(links.get('secret', '—'))}</code>",
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(is_disabled=True))


@router.message(Command("delnode"))
async def cmd_delnode(m: Message):
    if not _admin(m):
        return await _deny(m)
    parts = (m.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await m.answer("Формат: <code>/delnode &lt;имя&gt;</code> — удалит ноду любого типа "
                       "и все её прокси", parse_mode="HTML")
        return
    name = parts[1].strip()
    r = await _call(m, "DELETE", "/api/bot/nodes/" + quote(name, safe=""))
    if r is None:
        return
    await m.answer(f"🗑 Нода <b>{html.escape(name)}</b> удалена вместе с её прокси",
                   parse_mode="HTML")


@router.message(Command("nodes"))
async def cmd_nodes(m: Message):
    if not _admin(m):
        return await _deny(m)
    r = await _call(m, "POST", "/api/bot/nodes/check_all")
    if r is None:
        return
    rows = r.json().get("nodes", [])
    if not rows:
        await m.answer("Нод пока нет. Добавьте первую: /addnode")
        return
    lines = []
    for x in rows:
        mark = "🟢" if x.get("ok") else "🔴"
        lines.append(f"{mark} <b>{html.escape(x['name'])}</b> — "
                     f"{html.escape(x.get('proxy_host') or '—')} "
                     f"(http:{x.get('http')} socks:{x.get('socks')})")
    await m.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("newproxy"))
async def cmd_newproxy(m: Message):
    if not _admin(m):
        return await _deny(m)
    parts = (m.text or "").split(maxsplit=2)
    if len(parts) < 2:
        await m.answer("Формат: <code>/newproxy &lt;имя_ноды&gt; [метка]</code>",
                       parse_mode="HTML")
        return
    r = await _call(m, "POST", "/api/bot/creds", json={
        "node_name": parts[1],
        "label": parts[2] if len(parts) > 2 else "",
    })
    if r is None:
        return
    data = r.json()
    links = data.get("links", {})
    await m.answer(
        "✅ Прокси создан\n\n"
        f"Логин: <code>{data.get('username')}</code>\n"
        f"Пароль: <code>{data.get('password')}</code>\n\n"
        f"🌐 HTTP/HTTPS:\n<code>{html.escape(links.get('http', '—'))}</code>\n\n"
        f"🧦 SOCKS5:\n<code>{html.escape(links.get('socks', '—'))}</code>\n\n"
        f"✈️ Подключить в Telegram (нажми ссылку):\n{html.escape(links.get('tg', '—'))}",
        parse_mode="HTML",
        link_preview_options=LinkPreviewOptions(is_disabled=True))


@router.message(Command("proxies"))
async def cmd_proxies(m: Message):
    if not _admin(m):
        return await _deny(m)
    r = await _call(m, "GET", "/api/bot/creds")
    if r is None:
        return
    creds = r.json().get("creds", [])
    if not creds:
        await m.answer("Прокси пока нет. Создайте: /newproxy &lt;нода&gt;", parse_mode="HTML")
        return
    header = f"🗃 <b>Прокси ({len(creds)})</b>\n"
    body, chunks = "", []
    for c in creds[:50]:
        body += (
            f"\n<b>{html.escape(c['node_name'])}</b>"
            + (f" · {html.escape(c['label'])}" if c.get("label") else "") + "\n"
            f"<code>{c['username']}</code> : <code>{c['password']}</code>\n"
            f"{html.escape(c.get('proxy_host') or '—')} "
            f"(http:{c.get('proxy_http_port')} socks:{c.get('proxy_socks_port')})\n")
        if len(header) + len(body) > 3500:
            chunks.append(header + body)
            body = ""
    chunks.append(header + body)
    for ch in chunks:
        await m.answer(ch, parse_mode="HTML")


@router.message(Command("delproxy"))
async def cmd_delproxy(m: Message):
    if not _admin(m):
        return await _deny(m)
    parts = (m.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await m.answer("Формат: <code>/delproxy &lt;логин&gt;</code>", parse_mode="HTML")
        return
    username = parts[1].strip()
    r = await _call(m, "DELETE", "/api/bot/creds/" + quote(username, safe=""))
    if r is None:
        return
    await m.answer(f"🗑 Прокси <code>{html.escape(username)}</code> удалён",
                   parse_mode="HTML")


@router.message(Command("sync"))
async def cmd_sync(m: Message):
    if not _admin(m):
        return await _deny(m)
    parts = (m.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await m.answer("Формат: <code>/sync &lt;имя_ноды&gt;</code>", parse_mode="HTML")
        return
    name = parts[1].strip()
    r = await _call(m, "POST", "/api/bot/nodes/" + quote(name, safe="") + "/sync")
    if r is None:
        return
    synced = r.json().get("synced", 0)
    await m.answer(f"♻️ Синхронизировано {synced} прокси на ноду "
                   f"<b>{html.escape(name)}</b>", parse_mode="HTML")


@router.message(Command("stats"))
async def cmd_stats(m: Message):
    if not _admin(m):
        return await _deny(m)
    r = await _call(m, "GET", "/api/bot/stats")
    if r is None:
        return
    data = r.json()
    lines = []
    for n in data.get("nodes", []):
        if n.get("ok"):
            lines.append(f"🟢 {html.escape(n['name'])}: ▼ {_fmt(n.get('rx', 0))} / "
                         f"▲ {_fmt(n.get('tx', 0))} · соединений: {n.get('conns', 0)}")
        else:
            lines.append(f"🔴 {html.escape(n['name'])}: недоступна"
                         + (f" ({html.escape(n['error'])})" if n.get("error") else ""))
    top = data.get("top", [])
    if top:
        lines.append("\n<b>Топ пользователей:</b>")
        for u in top:
            lines.append(f"<code>{html.escape(u['user'])}</code>: {_fmt(u['rx'] + u['tx'])}")
    await m.answer("\n".join(lines) or "Нет данных", parse_mode="HTML")


def _fmt(n) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{int(n)} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


# ------------------------------------------------------------------------ запуск

async def start_bot() -> None:
    if not config.BOT_TOKEN:
        log.warning("BOT_TOKEN пуст — бот не запущен")
        return
    if not config.WEB_URL:
        log.error("WEB_URL пуст — боту некуда ходить за данными")
        return
    bot = Bot(token=config.BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception as exc:
        log.error("не удалось сбросить webhook (токен верный?): %s", exc)
        return
    log.info("бот запущен (long polling), web=%s", config.WEB_URL)
    try:
        await dp.start_polling(bot)
    except Exception as exc:
        log.error("ошибка polling: %s", exc)
    finally:
        await bot.session.close()
