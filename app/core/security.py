"""API-key generation, hashing and verification.

Key format: ``emp_<prefix8>_<secret>``. The 8-char prefix is stored in clear (indexed
lookup + safe to log); only ``sha256(pepper + full_key)`` is persisted. The full key is
shown exactly once, at creation time.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import string

from app.core.config import Settings, get_settings

KEY_SCHEME = "emp"
PREFIX_LENGTH = 8
_PREFIX_ALPHABET = string.ascii_lowercase + string.digits
_KEY_RE = re.compile(r"^emp_([a-z0-9]{8})_([A-Za-z0-9_-]{32,128})$")


def generate_api_key() -> tuple[str, str]:
    """Return ``(full_key, prefix)``. Persist only ``hash_api_key(full_key)`` and prefix."""
    prefix = "".join(secrets.choice(_PREFIX_ALPHABET) for _ in range(PREFIX_LENGTH))
    secret = secrets.token_urlsafe(32)
    return f"{KEY_SCHEME}_{prefix}_{secret}", prefix


def hash_api_key(full_key: str, settings: Settings | None = None) -> str:
    """sha256 hex digest of ``pepper + full_key``."""
    pepper = (settings or get_settings()).api_key_pepper.get_secret_value()
    return hashlib.sha256((pepper + full_key).encode("utf-8")).hexdigest()


def verify_api_key(full_key: str, stored_hash: str, settings: Settings | None = None) -> bool:
    """Constant-time comparison of the presented key's hash with the stored hash."""
    return hmac.compare_digest(hash_api_key(full_key, settings), stored_hash)


def parse_key_prefix(full_key: str) -> str | None:
    """Return the key prefix if ``full_key`` is well formed, else ``None``."""
    match = _KEY_RE.fullmatch(full_key)
    return match.group(1) if match else None
