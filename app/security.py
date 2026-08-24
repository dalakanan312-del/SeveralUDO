from __future__ import annotations

import hashlib
import hmac
import secrets


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def verify_secret(value: str, expected: str) -> bool:
    return hmac.compare_digest(hash_secret(value), expected)


def token(bytes_: int = 32) -> str:
    return secrets.token_urlsafe(bytes_)
