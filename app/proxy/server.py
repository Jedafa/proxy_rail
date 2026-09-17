"""Асинхронный прокси-движок: HTTP (forward) + HTTPS (CONNECT) + SOCKS5.

Один процесс, чистый asyncio. Авторизация по логину/паролю из AuthStore,
учёт трафика через Stats.
"""

import asyncio
import base64
import logging
import socket
import struct
from urllib.parse import urlsplit

from .. import config
from .auth_store import AuthStore
from .stats import Stats

log = logging.getLogger("proxy.engine")

REPLY_OK = b"HTTP/1.1 200 Connection established\r\n\r\n"
REPLY_BAD_REQUEST = b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
REPLY_AUTH_REQUIRED = (
    b"HTTP/1.1 407 Proxy Authentication Required\r\n"
    b'Proxy-Authenticate: Basic realm="proxy_rail"\r\n'
    b"Content-Length: 0\r\nConnection: close\r\n\r\n"
)
REPLY_FORBIDDEN = b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
REPLY_BAD_GATEWAY = b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"

# Заголовки, которые нельзя пробрасывать на upstream (hop-by-hop)
HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "proxy-connection", "te", "trailer", "upgrade", "transfer-encoding",
}


# --------------------------------------------------------------------------- utils

async def _read_head(reader: asyncio.StreamReader) -> bytes | None:
    """Читает заголовки запроса до CRLFCRLF с ограничением размера."""
    buf = b""
    while b"\r\n\r\n" not in buf:
        try:
            chunk = await asyncio.wait_for(reader.read(8192), timeout=60)
        except asyncio.TimeoutError:
            return None
        if not chunk:
            break
        buf += chunk
        if len(buf) > 65536:
            return None
    if b"\r\n\r\n" not in buf:
        return None
    return buf


def _parse_target(target: str, default_port: int) -> tuple[str | None, int | None]:
    """Парсит 'host:port', '[::1]:port' или абсолютный URL."""
    target = target.strip()
    if not target:
        return None, None
    if target.startswith("["):  # IPv6
        try:
            host, _, rest = target[1:].partition("]")
            port = int(rest.lstrip(":") or default_port)
            return host, port
        except ValueError:
            return None, None
    if "://" in target:
        try:
            parts = urlsplit(target)
            return parts.hostname, parts.port or default_port
        except ValueError:
            return None, None
    if ":" in target:
        host, _, port_s = target.rpartition(":")
        try:
            return host, int(port_s)
        except ValueError:
            return None, None
    return target, default_port


def _proxy_credentials(raw_head: bytes) -> tuple[str, str] | None:
    """Достаёт (user, password) из Proxy-Authorization: Basic ..."""
    for line in raw_head.split(b"\r\n"):
        if b":" not in line:
            continue
        name, _, value = line.partition(b":")
        if name.strip().lower() == b"proxy-authorization":
            value = value.strip()
            if value[:6].lower() == b"basic ":
                try:
                    decoded = base64.b64decode(value[6:].strip()).decode("utf-8", "replace")
                except Exception:
                    return None
                user, _, pwd = decoded.partition(":")
                return user, pwd
    return None


def _authorize(auth: AuthStore, creds) -> str | None:
    """Возвращает имя пользователя или None (407)."""
    if creds and auth.check(creds[0], creds[1]):
        return creds[0]
    if config.ALLOW_ANONYMOUS and not creds:
        return "anonymous"
    return None


def _header(header_lines: list[bytes], name: str) -> str | None:
    lname = name.lower().encode()
    for ln in header_lines:
        if b":" not in ln:
            continue
        n, _, v = ln.partition(b":")
        if n.strip().lower() == lname:
            return v.strip().decode("latin1")
    return None


def _close(*writers) -> None:
    for w in writers:
        try:
            w.close()
        except Exception:
            pass


# ------------------------------------------------------------------- forward HTTP

def _build_forward_request(
    method: str, path: str, header_lines: list[bytes], body: bytes, host: str, port: int
) -> bytes:
    out = [f"{method} {path} HTTP/1.1".encode("latin1")]
    has_host = False
    for ln in header_lines:
        if b":" not in ln or not ln.strip():
            continue
        name = ln.split(b":", 1)[0].strip().lower()
        if name in HOP_HEADERS or name in (b"content-length", b"expect"):
            continue
        if name == b"host":
            has_host = True
        out.append(ln)
    if not has_host:
        host_value = host if port == 80 else f"{host}:{port}"
        out.append(f"Host: {host_value}".encode("latin1"))
    out.append(b"Connection: close")
    if body:
        out.append(f"Content-Length: {len(body)}".encode())
    head = b"\r\n".join(out)
    return head + b"\r\n\r\n" + body


async def _read_chunked_body(reader: asyncio.StreamReader, initial: bytes, limit: int) -> bytes:
    data = initial
    out = bytearray()
    while True:
        while b"\r\n" not in data:
            data += await asyncio.wait_for(reader.read(8192), timeout=60)
        line, _, data = data.partition(b"\r\n")
        size = int(line.split(b";")[0].strip() or b"0", 16)
        if size == 0:
            return bytes(out)
        while len(data) < size + 2:
            data += await asyncio.wait_for(reader.read(8192), timeout=60)
        out += data[:size]
        if len(out) > limit:
            raise ValueError("body too large")
        data = data[size + 2:]


# ------------------------------------------------------------------- tunnel / pipe

async def _pipe(
    cr: asyncio.StreamReader,
    cw: asyncio.StreamWriter,
    leftover: bytes,
    ur: asyncio.StreamReader,
    uw: asyncio.StreamWriter,
    stats: Stats,
    user: str | None,
) -> None:
    """Двунаправленный туннель client <-> upstream с подсчётом трафика."""
    stats.conn_open(user)
    counters = {"rx": 0, "tx": len(leftover)}

    async def pump(src: asyncio.StreamReader, dst: asyncio.StreamWriter, key: str) -> None:
        try:
            if leftover and key == "tx":
                dst.write(leftover)
                await dst.drain()
            while True:
                chunk = await asyncio.wait_for(src.read(65536), timeout=config.IDLE_TIMEOUT)
                if not chunk:
                    break
                counters[key] += len(chunk)
                dst.write(chunk)
                await dst.drain()
        except (asyncio.TimeoutError, ConnectionError, OSError):
            pass
        finally:
            try:
                dst.write_eof()
            except (OSError, RuntimeError):
                pass

    try:
        await asyncio.gather(pump(cr, uw, "tx"), pump(ur, cw, "rx"))
    finally:
        stats.add(user, rx=counters["rx"], tx=counters["tx"])
        _close(cw, uw)


# ---------------------------------------------------------------------- HTTP proxy

async def handle_http(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                      auth: AuthStore, stats: Stats) -> None:
    try:
        head = await _read_head(reader)
        if head is None:
            writer.write(REPLY_BAD_REQUEST)
            await writer.drain()
            return
        raw_head, leftover = head.split(b"\r\n\r\n", 1)
        lines = raw_head.split(b"\r\n")
        try:
            method, target, _version = lines[0].decode("latin1").split(" ", 2)
        except ValueError:
            writer.write(REPLY_BAD_REQUEST)
            await writer.drain()
            return
        method = method.upper()

        creds = _proxy_credentials(raw_head)
        user = _authorize(auth, creds)
        if user is None:
            writer.write(REPLY_AUTH_REQUIRED)
            await writer.drain()
            return

        # ---------- HTTPS через CONNECT ----------
        if method == "CONNECT":
            host, port = _parse_target(target, 443)
            if not host or not port:
                writer.write(REPLY_BAD_REQUEST)
                await writer.drain()
                return
            try:
                ur, uw = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=30)
            except Exception as exc:
                log.info("CONNECT %s:%s failed: %s (user=%s)", host, port, exc, user)
                writer.write(REPLY_BAD_GATEWAY)
                await writer.drain()
                return
            writer.write(REPLY_OK)
            await writer.drain()
            log.info("CONNECT %s:%s established (user=%s)", host, port, user)
            await _pipe(reader, writer, leftover, ur, uw, stats, user)
            return

        # ---------- Обычный HTTP (absolute-URI или origin-form) ----------
        header_lines = lines[1:]
        if target.startswith("http://"):
            parts = urlsplit(target)
            host = parts.hostname
            port = parts.port or 80
            path = parts.path or "/"
            if parts.query:
                path += "?" + parts.query
        else:
            host_hdr = _header(header_lines, "host")
            if not host_hdr:
                writer.write(REPLY_BAD_REQUEST)
                await writer.drain()
                return
            host, port = _parse_target(host_hdr, 80)
            path = target
        if not host or not port:
            writer.write(REPLY_BAD_REQUEST)
            await writer.drain()
            return

        body = leftover
        clen_raw = _header(header_lines, "content-length")
        if clen_raw:
            try:
                clen = int(clen_raw)
            except ValueError:
                writer.write(REPLY_BAD_REQUEST)
                await writer.drain()
                return
            if clen > config.MAX_BODY:
                writer.write(REPLY_FORBIDDEN)
                await writer.drain()
                return
            if len(body) < clen:
                try:
                    body += await asyncio.wait_for(reader.readexactly(clen - len(body)), timeout=60)
                except (asyncio.IncompleteReadError, asyncio.TimeoutError):
                    return
            else:
                body = body[:clen]
        elif _header(header_lines, "transfer-encoding"):
            try:
                body = await _read_chunked_body(reader, body, config.MAX_BODY)
            except (ValueError, asyncio.TimeoutError, OSError):
                writer.write(REPLY_BAD_REQUEST)
                await writer.drain()
                return

        try:
            ur, uw = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=30)
        except Exception as exc:
            log.info("upstream %s:%s failed: %s (user=%s)", host, port, exc, user)
            writer.write(REPLY_BAD_GATEWAY)
            await writer.drain()
            return

        req = _build_forward_request(method, path, header_lines, body, host, port)
        stats.conn_open(user)
        tx, rx = len(req), 0
        try:
            uw.write(req)
            await uw.drain()
            while True:
                chunk = await asyncio.wait_for(ur.read(65536), timeout=config.IDLE_TIMEOUT)
                if not chunk:
                    break
                rx += len(chunk)
                writer.write(chunk)
                await writer.drain()
        except (asyncio.TimeoutError, ConnectionError, OSError):
            pass
        finally:
            stats.add(user, rx=rx, tx=tx)
            _close(uw, writer)
    except (ConnectionError, OSError) as exc:
        log.debug("http connection error: %s", exc)
    finally:
        _close(writer)


# -------------------------------------------------------------------- SOCKS5 proxy

async def _socks_reply(writer: asyncio.StreamWriter, code: int) -> None:
    # VER=5, REP=code, RSV=0, ATYP=IPv4, BND.ADDR=0.0.0.0, BND.PORT=0
    writer.write(b"\x05" + bytes([code]) + b"\x00\x01" + b"\x00\x00\x00\x00" + b"\x00\x00")
    await writer.drain()


async def handle_socks(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                       auth: AuthStore, stats: Stats) -> None:
    user = None
    try:
        try:
            head = await asyncio.wait_for(reader.readexactly(2), timeout=60)
        except (asyncio.IncompleteReadError, asyncio.TimeoutError):
            return
        ver, nmethods = head[0], head[1]
        if ver != 5 or nmethods == 0:
            return
        methods = await asyncio.wait_for(reader.readexactly(nmethods), timeout=30)

        offers: list[int] = []
        if auth.has_users():
            offers.append(0x02)  # username/password
        if config.ALLOW_ANONYMOUS:
            offers.append(0x00)  # no auth
        chosen = next((m for m in methods if m in offers), None)
        if chosen is None:
            writer.write(b"\x05\xff")
            await writer.drain()
            return
        writer.write(bytes([0x05, chosen]))
        await writer.drain()

        if chosen == 0x02:
            try:
                await asyncio.wait_for(reader.readexactly(1), timeout=30)  # ver=0x01
                ulen = (await asyncio.wait_for(reader.readexactly(1), timeout=30))[0]
                uname = (await asyncio.wait_for(reader.readexactly(ulen), timeout=30)).decode(
                    "utf-8", "replace")
                plen = (await asyncio.wait_for(reader.readexactly(1), timeout=30))[0]
                passwd = (await asyncio.wait_for(reader.readexactly(plen), timeout=30)).decode(
                    "utf-8", "replace")
            except (asyncio.IncompleteReadError, asyncio.TimeoutError):
                return
            if not auth.check(uname, passwd):
                writer.write(b"\x01\x01")
                await writer.drain()
                return
            writer.write(b"\x01\x00")
            await writer.drain()
            user = uname

        try:
            hdr = await asyncio.wait_for(reader.readexactly(4), timeout=60)
        except (asyncio.IncompleteReadError, asyncio.TimeoutError):
            return
        ver, cmd, _rsv, atyp = hdr[0], hdr[1], hdr[2], hdr[3]
        if ver != 5:
            return
        if cmd != 0x01:  # поддерживаем только CONNECT (BIND/UDP ASSOCIATE — в roadmap)
            await _socks_reply(writer, 0x07)
            return

        try:
            if atyp == 0x01:  # IPv4
                host = socket.inet_ntoa(await asyncio.wait_for(reader.readexactly(4), timeout=30))
            elif atyp == 0x03:  # домен
                ln = (await asyncio.wait_for(reader.readexactly(1), timeout=30))[0]
                host = (await asyncio.wait_for(reader.readexactly(ln), timeout=30)).decode(
                    "utf-8", "replace")
            elif atyp == 0x04:  # IPv6
                host = socket.inet_ntop(
                    socket.AF_INET6, await asyncio.wait_for(reader.readexactly(16), timeout=30))
            else:
                await _socks_reply(writer, 0x08)
                return
            port = struct.unpack("!H", await asyncio.wait_for(reader.readexactly(2), timeout=30))[0]
        except (asyncio.IncompleteReadError, asyncio.TimeoutError):
            return

        try:
            ur, uw = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=30)
        except Exception as exc:
            log.info("SOCKS CONNECT %s:%s failed: %s (user=%s)", host, port, exc, user)
            await _socks_reply(writer, 0x05)
            return
        await _socks_reply(writer, 0x00)
        log.info("SOCKS %s:%s established (user=%s)", host, port, user)
        await _pipe(reader, writer, b"", ur, uw, stats, user)
    except (ConnectionError, OSError) as exc:
        log.debug("socks connection error: %s", exc)
    finally:
        _close(writer)


# ------------------------------------------------------------------------ старт

async def start_servers(auth: AuthStore, stats: Stats) -> list[asyncio.AbstractServer]:
    async def http_cb(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        await handle_http(r, w, auth, stats)

    async def socks_cb(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        await handle_socks(r, w, auth, stats)

    http_srv = await asyncio.start_server(http_cb, config.HOST, config.HTTP_PROXY_PORT)
    socks_srv = await asyncio.start_server(socks_cb, config.HOST, config.SOCKS_PORT)
    log.info("HTTP/HTTPS proxy слушает %s:%s", config.HOST, config.HTTP_PROXY_PORT)
    log.info("SOCKS5 proxy слушает %s:%s", config.HOST, config.SOCKS_PORT)
    return [http_srv, socks_srv]
