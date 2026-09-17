#!/bin/sh
# Первичный запуск WEB-прокси Telegram на VPS.
# Использование:  sudo ./setup.sh          (после заполнения .env)
set -eu

cd "$(dirname "$0")"

if [ "$(id -u)" != "0" ]; then
  echo "Запустите от root: sudo ./setup.sh"; exit 1
fi

if [ ! -f .env ]; then
  echo ".env не найден. Скопируйте шаблон и заполните:"
  echo "  cp .env.example .env && nano .env"
  exit 1
fi

. ./.env

# --- проверки ---
if ! printf '%s' "$WEB_HOSTNAME" | grep -qE '^[a-z0-9.-]+$'; then
  echo "WEB_HOSTNAME должен быть строчным доменом без https:// и путей: $WEB_HOSTNAME"
  exit 1
fi
if ! printf '%s' "$WEB_SECRET" | grep -qE '^[0-9a-f]{32}$'; then
  echo "WEB_SECRET должен быть 32 hex-символа: openssl rand -hex 16"
  exit 1
fi
if [ -n "${BASE_PATH:-}" ]; then
  echo "ПРЕДУПРЕЖДЕНИЕ: задан BASE_PATH — в ссылках бота секрет будет в помеченной base64url-форме."
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker не найден. Установите: curl -fsSL https://get.docker.com | sh"
  exit 1
fi

echo "==> Проверка DNS: $WEB_HOSTNAME"
IP_RESOLVED=$(getent hosts "$WEB_HOSTNAME" | awk '{print $1; exit}' || true)
IP_PUBLIC=$(curl -fsS4 --max-time 8 https://api.ipify.org 2>/dev/null || true)
if [ -z "$IP_RESOLVED" ]; then
  echo "  Домен не резолвится. Добавьте A-запись: $WEB_HOSTNAME -> $IP_PUBLIC"
  exit 1
fi
if [ -n "$IP_PUBLIC" ] && [ "$IP_RESOLVED" != "$IP_PUBLIC" ]; then
  echo "  ВНИМАНИЕ: $WEB_HOSTNAME -> $IP_RESOLVED, а публичный IP сервера: $IP_PUBLIC."
  echo "  Домен должен указывать на ЭТОТ сервер, иначе Let's Encrypt не выпустит сертификат."
  exit 1
fi
echo "  DNS в порядке ($IP_RESOLVED)"

echo "==> Сборка и запуск (caddy + tproxy + mtproxy)"
docker compose up -d --build

sleep 3
if curl -fsS --max-time 8 "http://127.0.0.1:8081/healthz" >/dev/null 2>&1; then
  echo "  релей: healthz OK"
fi
curl -fsS --max-time 8 "https://$WEB_HOSTNAME/" >/dev/null && echo "  сайт: https://$WEB_HOSTNAME/ отвечает"

echo
echo "Готово. Проверьте через 1-2 минуты:"
echo "  docker compose ps"
echo "  docker compose logs -f mtproxy   (не должно быть потока 'Disconnected from RPC Middle-End')"
echo
echo "Ссылка для Telegram:"
echo "  https://t.me/webproxy?server=$WEB_HOSTNAME&secret=$WEB_SECRET"
echo
echo "Затем добавьте прокси в менеджер: /addtgweb Имя|$WEB_HOSTNAME|$WEB_SECRET"
