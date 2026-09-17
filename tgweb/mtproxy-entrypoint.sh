#!/bin/sh
# Подтягивает официальные конфиг/секрет MTProxy и запускает прокси.
# Отдельный каталог /data нужен, чтобы Caddy слушал 80/443, а 2398 не торчал наружу.
set -eu

cd /data

fetch() {
  curl -fsSL --retry 3 --retry-delay 2 -o "$1" "$2"
}

# Официальные данные Telegram DC (обновляются время от времени)
fetch proxy-secret  https://core.telegram.org/getProxySecret   || true
fetch proxy-multi.conf https://core.telegram.org/getProxyConfig || true

[ -s proxy-secret ]      || { echo "не удалось скачать proxy-secret";  exit 1; }
[ -s proxy-multi.conf ]  || { echo "не удалось скачать proxy-multi.conf"; exit 1; }

refresh_loop() {
  while :; do
    sleep 43200   # 12 часов
    fetch proxy-secret  https://core.telegram.org/getProxySecret   && \
    fetch proxy-multi.conf https://core.telegram.org/getProxyConfig || true
  done
}
refresh_loop &

WORKERS="${MTPROXY_WORKERS:-1}"
MAXCONNS="${MTPROXY_MAX_CONNECTIONS:-4096}"

set -- \
  -u nobody \
  -p 2398 \
  -H 2398 \
  -M "${WORKERS}" \
  --max-special-connections "${MAXCONNS}" \
  --aes-pwd proxy-secret proxy-multi.conf \
  --allow-skip-dh

# ВАЖНО: если хост за NAT (облачные VPS, контейнеры, Railway) — middle-end
# соединения будут молча рваться. Тогда обязательно задайте MTPROXY_NAT_INFO
# в формате "внутренний_ip:публичный_ip", например "10.0.0.5:203.0.113.7".
if [ -n "${MTPROXY_NAT_INFO:-}" ]; then
  set -- "$@" --nat-info "${MTPROXY_NAT_INFO}"
fi

echo "[mtproxy] запуск: mtproto-proxy $*"
exec /usr/local/bin/mtproto-proxy "$@"
