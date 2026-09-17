"""Telegram-бот управления proxy_rail (aiogram 3, long polling).

Доступ только у Telegram ID из переменной ADMIN_TG_IDS.
"""

import html
import logging

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import LinkPreviewOptions, Message

from . import config, db
from .core_service import (build_links, check_node, create_cred, delete_cred,
                           get_cred_by_username, get_node_by_name, list_creds, list_nodes,
                           node_request, sync_node, fmt_bytes)

log = logging.getLogger("bot")
router = Router()

NO_ACCESS = "⛔ Нет доступа. Ваш Telegram ID: <code>{id}</code> — добавьте его в ADMIN_TG_IDS"

HELP_TEXT = (
    "🛡 <b>proxy_rail</b> — управление прокси\n\n"
    "/nodes — список серверов и статус\n"
    "/addnode — добавить сервер (ноду)\n"
    "/newproxy &lt;нода&gt; [метка] — создать прокси\n"
    "/proxies — список прокси\n"
    "/delproxy &lt;логин&gt; — удалить прокси\n"
    "/sync &lt;нода&gt; — перезалить прокси на ноду\n"
    "/stats — трафик по нодам\n"
    "/help — эта справка"
)


def _admin(m: Message) -> bool:
    return bool(m.from_user and m.from_user.id in config.ADMIN_TG_IDS)


# ------------------------------------------------------------------------ команды

@router.message(CommandStart())
async def cmd_start(m: Message):
    if not _admin(m):
        await m.answer(NO_ACCESS.format(id=m.from_user.id if m.from_user else "?"),
                       parse_mode="HTML")
        return
    await m.answer(HELP_TEXT, parse_mode="HTML")


@router.message(Command("help"))
async def cmd_help(m: Message):
    if not _admin(m):
        await m.answer(NO_ACCESS.format(id=m.from_user.id if m.from_user else "?"),
                       parse_mode="HTML")
        return
    await m.answer(HELP_TEXT, parse_mode="HTML")


@router.message(Command("addnode"))
async def cmd_addnode(m: Message):
    if not _admin(m):
        return await m.answer(NO_ACCESS.format(id=m.from_user.id if m.from_user else "?"),
                              parse_mode="HTML")
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
    from .core_service import upsert_node
    upsert_node(name, url.rstrip("/"), token, host, hport_i, sport_i)
    node = get_node_by_name(name)
    ok = await check_node(node) if node else False
    if ok:
        await m.answer(f"🟢 Нода <b>{html.escape(name)}</b> добавлена, соединение в порядке",
                       parse_mode="HTML")
    else:
        await m.answer(f"🟠 Нода <b>{html.escape(name)}</b> добавлена, но не отвечает — "
                       "проверьте URL и токен (/nodes)", parse_mode="HTML")


@router.message(Command("nodes"))
async def cmd_nodes(m: Message):
    if not _admin(m):
        return await m.answer(NO_ACCESS.format(id=m.from_user.id if m.from_user else "?"),
                              parse_mode="HTML")
    nodes = list_nodes()
    if not nodes:
        await m.answer("Нод пока нет. Добавьте первую: /addnode")
        return
    lines = []
    for n in nodes:
        ok = await check_node(n)
        mark = "🟢" if ok else "🔴"
        host = n["proxy_host"] or "—"
        lines.append(f"{mark} <b>{html.escape(n['name'])}</b> — {html.escape(host)} "
                     f"(http:{n['proxy_http_port']} socks:{n['proxy_socks_port']})")
    await m.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("newproxy"))
async def cmd_newproxy(m: Message):
    if not _admin(m):
        return await m.answer(NO_ACCESS.format(id=m.from_user.id if m.from_user else "?"),
                              parse_mode="HTML")
    parts = (m.text or "").split(maxsplit=2)
    if len(parts) < 2:
        await m.answer("Формат: <code>/newproxy &lt;имя_ноды&gt; [метка]</code>",
                       parse_mode="HTML")
        return
    node = get_node_by_name(parts[1])
    if not node:
        await m.answer("Нода не найдена. Список: /nodes")
        return
    label = parts[2] if len(parts) > 2 else ""
    u, p, err = await create_cred(node, "", "", label, m.from_user.id if m.from_user else None)
    if err:
        await m.answer("❌ " + err)
        return
    host = node["proxy_host"] or "NODE_HOST_NOT_SET"
    host_e = html.escape(host)
    await m.answer(
        f"✅ Прокси создан на <b>{html.escape(node['name'])}</b>"
        + (f" ({html.escape(label)})" if label else "") + "\n\n"
        f"Логин: <code>{u}</code>\nПароль: <code>{p}</code>\n\n"
        f"🌐 HTTP/HTTPS:\n<code>http://{u}:{p}@{host_e}:{node['proxy_http_port']}</code>\n\n"
        f"🧦 SOCKS5:\n<code>socks5://{u}:{p}@{host_e}:{node['proxy_socks_port']}</code>\n\n"
        f"✈️ Подключить в Telegram (нажми ссылку):\n"
        f"https://t.me/socks?server={host_e}&amp;port={node['proxy_socks_port']}"
        f"&amp;user={u}&amp;pass={p}",
        parse_mode="HTML",
        link_preview_options=LinkPreviewOptions(is_disabled=True))


@router.message(Command("proxies"))
async def cmd_proxies(m: Message):
    if not _admin(m):
        return await m.answer(NO_ACCESS.format(id=m.from_user.id if m.from_user else "?"),
                              parse_mode="HTML")
    creds = list_creds()
    if not creds:
        await m.answer("Прокси пока нет. Создайте: /newproxy &lt;нода&gt;", parse_mode="HTML")
        return
    header = f"🗃 <b>Прокси ({len(creds)})</b>\n"
    body = ""
    chunks: list[str] = []
    for c in creds[:50]:
        body += (
            f"\n<b>{html.escape(c['node_name'])}</b>"
            + (f" · {html.escape(c['label'])}" if c["label"] else "") + "\n"
            f"<code>{c['username']}</code> : <code>{c['password']}</code>\n"
            f"{html.escape(c['proxy_host'] or '—')} "
            f"(http:{c['proxy_http_port']} socks:{c['proxy_socks_port']})\n")
        if len(header) + len(body) > 3500:
            chunks.append(header + body)
            body = ""
    chunks.append(header + body)
    for ch in chunks:
        await m.answer(ch, parse_mode="HTML")


@router.message(Command("delproxy"))
async def cmd_delproxy(m: Message):
    if not _admin(m):
        return await m.answer(NO_ACCESS.format(id=m.from_user.id if m.from_user else "?"),
                              parse_mode="HTML")
    parts = (m.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await m.answer("Формат: <code>/delproxy &lt;логин&gt;</code>", parse_mode="HTML")
        return
    cred = get_cred_by_username(parts[1].strip())
    if not cred:
        await m.answer("Прокси с таким логином не найден")
        return
    await delete_cred(cred)
    await m.answer(f"🗑 Прокси <code>{html.escape(parts[1].strip())}</code> удалён",
                   parse_mode="HTML")


@router.message(Command("sync"))
async def cmd_sync(m: Message):
    if not _admin(m):
        return await m.answer(NO_ACCESS.format(id=m.from_user.id if m.from_user else "?"),
                              parse_mode="HTML")
    parts = (m.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await m.answer("Формат: <code>/sync &lt;имя_ноды&gt;</code>", parse_mode="HTML")
        return
    node = get_node_by_name(parts[1].strip())
    if not node:
        await m.answer("Нода не найдена. Список: /nodes")
        return
    ok = await sync_node(node)
    await m.answer(f"♻️ Синхронизировано {ok} прокси на ноду "
                   f"<b>{html.escape(node['name'])}</b>", parse_mode="HTML")


@router.message(Command("stats"))
async def cmd_stats(m: Message):
    if not _admin(m):
        return await m.answer(NO_ACCESS.format(id=m.from_user.id if m.from_user else "?"),
                              parse_mode="HTML")
    total: dict[str, list[int]] = {}
    lines = []
    for n in list_nodes():
        try:
            r = await node_request(n, "GET", "/api/stats", timeout=8)
            if r.status_code != 200:
                lines.append(f"🔴 {html.escape(n['name'])}: недоступна (HTTP {r.status_code})")
                continue
            data = r.json()
            rx = tx = 0
            for uname, v in (data.get("users") or {}).items():
                rx += v.get("rx", 0)
                tx += v.get("tx", 0)
                acc = total.setdefault(uname, [0, 0])
                acc[0] += v.get("rx", 0)
                acc[1] += v.get("tx", 0)
            lines.append(f"🟢 {html.escape(n['name'])}: ▼ {fmt_bytes(rx)} / "
                         f"▲ {fmt_bytes(tx)} · соединений: {data.get('total_conns', 0)}")
        except Exception:
            lines.append(f"🔴 {html.escape(n['name'])}: недоступна")
    if not lines:
        await m.answer("Нод нет. Добавьте: /addnode")
        return
    top = sorted(total.items(), key=lambda kv: -(kv[1][0] + kv[1][1]))[:10]
    if top:
        lines.append("\n<b>Топ пользователей:</b>")
        for uname, (trx, ttx) in top:
            lines.append(f"<code>{html.escape(uname)}</code>: {fmt_bytes(trx + ttx)}")
    await m.answer("\n".join(lines), parse_mode="HTML")


# ------------------------------------------------------------------------ запуск

async def start_bot() -> None:
    if not config.BOT_TOKEN:
        log.warning("BOT_TOKEN пуст — бот не запущен")
        return
    bot = Bot(token=config.BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception as exc:
        log.error("не удалось сбросить webhook (токен верный?): %s", exc)
        return
    log.info("бот запущен (long polling)")
    try:
        await dp.start_polling(bot)
    except Exception as exc:
        log.error("ошибка polling: %s", exc)
    finally:
        await bot.session.close()
