import hashlib
import hmac
import secrets


def gen_username(prefix: str = "px") -> str:
    return f"{prefix}_{secrets.token_hex(3)}"


def gen_password(n: int = 16) -> str:
    return secrets.token_urlsafe(16)[:n]


def hash_password(pw: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(8)
    h = hashlib.sha256((salt + ":" + pw).encode()).hexdigest()
    return f"{salt}${h}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        salt, _ = stored.split("$", 1)
    except ValueError:
        return False
    return hmac.compare_digest(hash_password(pw, salt), stored)


def check_token(provided: str, expected: str) -> bool:
    if not expected or not provided:
        return False
    return hmac.compare_digest(provided, expected)
