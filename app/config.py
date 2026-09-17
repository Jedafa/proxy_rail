import os
import secrets


def _bool(v: str) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")


# Роль процесса: core (админка + бот) | node (прокси-сервер) | all (всё в одном, для локальной отладки)
ROLE = os.getenv("ROLE", "all").lower()
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))

DATA_DIR = os.getenv("DATA_DIR", os.path.join(os.getcwd(), "data"))
os.makedirs(DATA_DIR, exist_ok=True)
CORE_DB = os.getenv("CORE_DB", os.path.join(DATA_DIR, "core.db"))
NODE_DB = os.getenv("NODE_DB", os.path.join(DATA_DIR, "node.db"))

APP_VERSION = "0.1.0"

# ---------------- NODE (прокси-сервер) ----------------
# Токен для управления нодой (core -> node API). Обязателен для продакшена.
NODE_TOKEN = os.getenv("NODE_TOKEN", "")
# Порты прокси внутри контейнера/нод-сервиса
HTTP_PROXY_PORT = int(os.getenv("HTTP_PROXY_PORT", "8888"))
SOCKS_PORT = int(os.getenv("SOCKS_PORT", "1080"))
# Разрешить анонимный доступ к прокси (НЕ рекомендуется)
ALLOW_ANONYMOUS = _bool(os.getenv("ALLOW_ANONYMOUS", "false"))
MAX_BODY = int(os.getenv("MAX_BODY_MB", "50")) * 1024 * 1024
IDLE_TIMEOUT = int(os.getenv("IDLE_TIMEOUT", "600"))

# Авто-регистрация ноды в core (необязательно)
CORE_URL = os.getenv("CORE_URL", "").rstrip("/")
REG_TOKEN = os.getenv("REG_TOKEN", "")
NODE_NAME = os.getenv("NODE_NAME", "")
# Публичный адрес ноды (для авто-регистрации в core)
PUBLIC_CONTROL_URL = os.getenv("PUBLIC_CONTROL_URL", "").rstrip("/")
PUBLIC_PROXY_HOST = os.getenv("PUBLIC_PROXY_HOST", "")
PUBLIC_HTTP_PORT = int(os.getenv("PUBLIC_HTTP_PORT", "0"))
PUBLIC_SOCKS_PORT = int(os.getenv("PUBLIC_SOCKS_PORT", "0"))

# ---------------- CORE (админка + бот) ----------------
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
SESSION_SECRET = os.getenv("SESSION_SECRET", "") or secrets.token_hex(32)
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_TG_IDS = {
    int(x) for x in os.getenv("ADMIN_TG_IDS", "").replace(" ", "").split(",") if x.strip().isdigit()
}
