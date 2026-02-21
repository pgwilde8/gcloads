import hashlib
import secrets
from datetime import datetime, timedelta, timezone


def generate_magic_token() -> str:
    return secrets.token_urlsafe(32)


def hash_magic_token(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def token_expiry(ttl_minutes: int) -> datetime:
    ttl = max(int(ttl_minutes or 30), 1)
    return datetime.now(timezone.utc) + timedelta(minutes=ttl)
