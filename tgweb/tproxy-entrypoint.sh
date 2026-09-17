#!/bin/sh
# Рендерит конфиги tproxy-server из переменных окружения и запускает релей.
set -eu

: "${WEB_HOSTNAME:?WEB_HOSTNAME не задан}"
: "${WEB_SECRET:?WEB_SECRET не задан}"

BASE_PATH="${BASE_PATH:-}"
CARRIER_MODE="${CARRIER_MODE:-https}"
MT_PROXY_BACKEND="${MT_PROXY_BACKEND:-mtproxy:2398}"

mkdir -p /data

# Нормализация base_path: без ведущего/хвостового слэша
BASE_PATH=$(printf '%s' "$BASE_PATH" | sed 's|^/*||; s|/*$||')

cat > /data/config.json <<EOF
{
  "public_hostname": "${WEB_HOSTNAME}",
  "base_path": "${BASE_PATH}",
  "listen": "0.0.0.0:8080",
  "admin_listen": "0.0.0.0:8081",
  "public_dir": "/srv/site",
  "static_routes": "exact",
  "token_key_file": "/data/token.key",
  "profiles_file": "/data/profiles.json",
  "enable_pprof": false
}
EOF

cat > /data/profiles.json <<EOF
{
  "profiles": [
    {
      "name": "default",
      "secret": "${WEB_SECRET}",
      "backend": "${MT_PROXY_BACKEND}",
      "carrier_mode": "${CARRIER_MODE}"
    }
  ]
}
EOF

# Постоянный ключ подписи токенов: создаётся один раз и должен жить между рестартами,
# иначе старые сессии клиентов перестанут принимать сервер.
if [ ! -s /data/token.key ]; then
  head -c 32 /dev/urandom > /data/token.key
  chmod 600 /data/token.key
fi

echo "[tproxy] проверка конфигурации:"
/usr/local/bin/tproxy-server --config /data/config.json --check

exec /usr/local/bin/tproxy-server --config /data/config.json
