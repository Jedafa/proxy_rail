# proxy_rail

Панель управления прокси-серверами для деплоя на **Railway** (Docker).

**Три отдельных сервиса, собираются из одного репозитория одним Docker-образом** —
роли переключаются переменной `ROLE`. Хочешь много серверов — просто создавай
больше node-сервисов, бот и веб остаются как есть:

| Сервис | ROLE | Что делает |
|--------|------|------------|
| **web** | `web` | Веб-админка + единственная БД + внутренний API (для бота и нод). Мастер-сервис |
| **bot** | `bot` | Telegram-бот. Тонкий клиент: своей БД нет, всё берёт из web по HTTP |
| **node** | `node` | Прокси-сервер: **HTTP**, **HTTPS (CONNECT)**, **SOCKS5** + control API. Клонируй сколько нужно |

Протоколы прокси на ноде: HTTP + HTTPS + SOCKS5 с авторизацией логин/пароль, учёт
трафика по каждому пользователю. Для Telegram — готовые deep-links `t.me/socks?...`
(MTProto и TG Web-прокси — в roadmap).

```
                ┌──────────────────────────── Railway ────────────────────────┐
                │                                                             │
 Telegram ──────┤  BOT (ROLE=bot)          WEB (ROLE=web)        ВЫ ───►      │
                │  · aiogram polling ─────► · админка на $PORT                │
                │  · без БД, без порта      · SQLite (/data)                  │
                │         │                 · API: /api/bot, /api/nodes       │
                │         │                        │                          │
                │         │                 управляет ▼                      │
                │  NODE1 (ROLE=node) ◄────────────┘                           │
                │  HTTP/HTTPS :8888 · SOCKS5 :1080 · control API              │
                │                                                             │
                │  NODE2, NODE3, ... — столько, сколько нужно                 │
                └─────────────────────────────────────────────────────────────┘
```

Почему так: **web** — единственный владелец данных, рестарт бота ничего не ломает,
ноды добавляются/удаляются без касания панели, а веб и бот можно обновлять по отдельности.

---

## Быстрый старт (локально, docker compose)

```bash
cp .env.example .env       # впиши пароли и BOT_TOKEN
docker compose up --build
```

- Админка web: http://localhost:8000 (пароль из `ADMIN_PASSWORD`)
- Прокси ноды: `http://user:pass@localhost:8888` и `socks5://user:pass@localhost:1080`
- Control API ноды: http://localhost:8080 · health бота: http://localhost:8001 (в compose бот без внешнего порта)

Режим `ROLE=all` запускает админку + бота + прокси в одном процессе — удобно для
быстрой отладки на своём ПК (`python -m app.main`).

---

## Деплой на Railway (пошагово)

### 1. Сервис WEB (админка)

1. Railway → **New Project** → **Deploy from GitHub repo** → `proxy_rail`.
2. Variables:

   | Переменная | Значение |
   |---|---|
   | `ROLE` | `web` |
   | `ADMIN_PASSWORD` | ваш пароль админки |
   | `SESSION_SECRET` | длинная случайная строка |
   | `BOT_API_TOKEN` | случайная строка (придумайте, та же пойдёт боту) |
   | `REG_TOKEN` | случайная строка (придумайте, та же пойдёт нодам) |
   | `DATA_DIR` | `/data` |

3. **Settings → Networking → Generate Domain** (или **Custom Domain** — свой домен).
   Это адрес админки и `WEB_URL`/`CORE_URL` для бота и нод.
4. **Volumes → New Volume**, mount path: `/data` — чтобы БД пережила редеплои.
5. Откройте домен, войдите с паролем.

### 2. Сервис BOT (Telegram-бот)

1. В том же проекте: **New Service → GitHub repo → proxy_rail** (второй экземпляр).
2. Variables:

   | Переменная | Значение |
   |---|---|
   | `ROLE` | `bot` |
   | `BOT_TOKEN` | токен от @BotFather |
   | `ADMIN_TG_IDS` | ваш Telegram ID (узнать: @userinfobot), можно несколько через запятую |
   | `WEB_URL` | `https://<домен-web>.up.railway.app` |
   | `BOT_API_TOKEN` | тот же, что на web |

3. Домен и volume боту **не нужны** — он работает через long polling.

### 3. Сервис NODE (прокси-сервер) — клонируйте сколько нужно

Каждая нода = отдельный Railway-сервис из того же репозитория:

1. **New Service → GitHub repo → proxy_rail** (ещё один экземпляр).
2. Variables:

   | Переменная | Значение |
   |---|---|
   | `ROLE` | `node` |
   | `NODE_TOKEN` | случайный секрет ноды (понадобится в web) |
   | `NODE_NAME` | `node1` |
   | `HTTP_PROXY_PORT` | `8888` |
   | `SOCKS_PORT` | `1080` |
   | `DATA_DIR` | `/data` |
   | `CORE_URL` | `https://<домен-web>.up.railway.app` |
   | `REG_TOKEN` | тот же, что на web |
   | `PUBLIC_CONTROL_URL` | `https://<домен-ноды>` (после шага 3) |
   | `PUBLIC_PROXY_HOST` / `PUBLIC_HTTP_PORT` / `PUBLIC_SOCKS_PORT` | внешние адреса прокси (после шага 4) |

3. **Settings → Networking → Generate Domain** (или свой домен) — это control API
   ноды, по нему web управляет пользователями.
4. **Settings → Networking → TCP Proxy → Add**: один туннель для порта `8888`,
   второй для `1080`. Railway выдаст внешние `host:port` вида
   `xxxx.up.rlwy.net:31234` — это и есть адреса ваших прокси.
5. **Volumes → New Volume**, mount path: `/data` (иначе пользователи сбросятся при
   редеплое — лечится кнопкой «Синхронизировать» или `/sync` в боте).
6. Нода сама зарегистрируется в web (`CORE_URL` + `REG_TOKEN`), либо добавьте её
   вручную: админка → **Ноды** → Добавить, или в боте `/addnode`.

> Новая нода = ещё один сервис из этого же репо с новым `NODE_NAME`/`NODE_TOKEN`
> и своими TCP Proxy. Никаких изменений в коде — просто повторяйте шаги.

### 4. Про домены и прокси — важно понимать

Railway роутит **веб-трафик (HTTP/HTTPS) по домену** — поэтому свой домен вы
подключаете к админке и control API. Прокси-протоколы (SOCKS5 и HTTP CONNECT) —
это «сырой» TCP, для них Railway предоставляет **TCP Proxy** (`*.rlwy.net:порт`)
без привязки домена. Это нормально: клиенты подключаются к `host:port` от TCP Proxy.

Если нужны прокси именно на своём домене — задеплойте ноду на любой VPS тем же
Docker-образом (`docker run -d -p 8888:8888 -p 1080:1080 -e ROLE=node -e NODE_TOKEN=... <образ>`)
и повесьте домен туда (A-запись). Web при этом остаётся на Railway и управляет
такой нодой так же, по её URL.

### 5. Генерация прокси

- **Бот**: `/newproxy node1 КлиентИван` → готовое сообщение со всеми ссылками,
  включая кнопку-ссылку `t.me/socks?...` для подключения Telegram в один тап.
- **Админка**: раздел «Прокси» → Создать → copy-кнопки у каждой ссылки.

---

## Команды бота

| Команда | Описание |
|---|---|
| `/start`, `/help` | справка |
| `/nodes` | список нод и статус (пингует каждую) |
| `/addnode Имя\|URL\|Токен\|Host\|HTTPпорт\|SOCKSпорт` | добавить ноду |
| `/newproxy <нода> [метка]` | создать прокси-доступ, выдать все ссылки |
| `/proxies` | список всех прокси |
| `/delproxy <логин>` | удалить прокси (с ноды и из базы) |
| `/sync <нода>` | перезалить все доступы web на ноду |
| `/stats` | трафик по нодам + топ пользователей |

---

## Переменные окружения

| Переменная | Сервис | По умолчанию | Описание |
|---|---|---|---|
| `ROLE` | все | `all` | `web` / `bot` / `node` / `all` |
| `PORT` | все | `8000` | порт веб-сервиса (Railway задаёт сам) |
| `DATA_DIR` | web, node | `./data` | каталог SQLite (на Railway — `/data`) |
| `ADMIN_PASSWORD` | web | `admin123` | пароль входа в админку |
| `SESSION_SECRET` | web | random | секрет подписи cookie-сессий |
| `BOT_API_TOKEN` | web, bot | — | токен внутреннего API (бот → web), должен совпадать |
| `REG_TOKEN` | web, node | — | токен авто-регистрации нод |
| `BOT_TOKEN` | bot | — | токен бота |
| `ADMIN_TG_IDS` | bot | — | Telegram ID администраторов |
| `WEB_URL` | bot | — | адрес веб-панели для бота |
| `NODE_TOKEN` | node | — | токен control API ноды |
| `HTTP_PROXY_PORT` | node | `8888` | порт HTTP/HTTPS прокси |
| `SOCKS_PORT` | node | `1080` | порт SOCKS5 |
| `ALLOW_ANONYMOUS` | node | `false` | разрешить доступ без логина/пароля |
| `CORE_URL` | node | — | адрес web для авто-регистрации |
| `PUBLIC_CONTROL_URL` | node | — | публичный адрес самой ноды |
| `PUBLIC_PROXY_HOST/HTTP/SOCKS` | node | — | публичные адреса прокси (для автозаполнения в web) |

## Внутренние API

**Node control API** (заголовок `X-Node-Token: <NODE_TOKEN>`):
`GET /health` (без токена) · `GET /api/ping` · `GET/POST /api/users` ·
`DELETE /api/users/{username}` · `GET /api/stats`

**Web internal API**:
- для нод: `POST /api/nodes/register` (`X-Reg-Token`... в теле `reg_token`)
- для бота (заголовок `X-Bot-Token: <BOT_API_TOKEN>`): `GET/POST /api/bot/nodes`,
  `POST /api/bot/nodes/check_all`, `POST /api/bot/nodes/{name}/sync`,
  `POST /api/bot/creds`, `GET /api/bot/creds`, `DELETE /api/bot/creds/{username}`,
  `GET /api/bot/stats`

## Безопасность

- Обязательно смените `ADMIN_PASSWORD`, задайте `SESSION_SECRET`, `BOT_API_TOKEN`,
  `NODE_TOKEN`, `REG_TOKEN`.
- Прокси без авторизации не работает (`ALLOW_ANONYMOUS=false`).
- Пароли пользователей хранятся на ноде в виде salted SHA-256.
- Railway-домены публичны: админка защищена паролем, API — токенами.

## Roadmap

- [ ] MTProto-прокси для Telegram (отдельный порт + deep-link `t.me/proxy?...`)
- [ ] Новый вид TG Web-прокси (web view)
- [ ] Лимиты трафика и срока жизни на прокси-доступ
- [ ] Ротация паролей одной кнопкой
- [ ] IP-allowlist на нодах
- [ ] Prometheus-метрики
