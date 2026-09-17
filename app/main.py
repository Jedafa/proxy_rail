"""Точка входа proxy_rail.

Роли задаются переменной окружения ROLE:
  web  — веб-админка + внутренний API (мастер-сервис с БД)
  bot  — Telegram-бот (тонкий клиент web-API)
  node — прокси-сервер (HTTP/HTTPS/SOCKS5) + control API
  all  — всё в одном процессе (локальная отладка)

Запуск: python -m app.main
"""

import logging

import uvicorn

from . import config

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
log = logging.getLogger("main")


def main() -> None:
    role = config.ROLE
    if role not in ("web", "bot", "node", "all"):
        raise SystemExit("ROLE должен быть: web | bot | node | all")
    log.info("proxy_rail v%s | role=%s | порт=%s", config.APP_VERSION, role, config.PORT)
    if role in ("web", "all") and config.ADMIN_PASSWORD == "admin123":
        log.warning("ADMIN_PASSWORD не задан — используется пароль по умолчанию, СМЕНИТЕ ЕГО!")
    if role == "node":
        from .node_api import app
    elif role == "bot":
        from .bot_api import app
    else:
        from .web_api import app
    uvicorn.run(app, host=config.HOST, port=config.PORT,
                log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
