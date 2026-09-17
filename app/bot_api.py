"""Отдельный сервис Telegram-бота: минимальный HTTP (/health) + long polling в фоне."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import config

log = logging.getLogger("bot")


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = None
    if config.BOT_TOKEN:
        if not config.WEB_URL:
            log.error("WEB_URL не задан — боту некуда ходить за данными!")
        else:
            from .bot import start_bot
            task = asyncio.create_task(start_bot())
    else:
        log.warning("BOT_TOKEN пуст — бот не запущен")
    yield
    if task:
        task.cancel()


app = FastAPI(title="proxy_rail bot", version=config.APP_VERSION,
              lifespan=lifespan, docs_url=None, redoc_url=None)


@app.get("/health")
async def health():
    return {"ok": True, "role": "bot", "version": config.APP_VERSION,
            "bot_enabled": bool(config.BOT_TOKEN), "web_url_set": bool(config.WEB_URL)}
