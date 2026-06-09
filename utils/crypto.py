"""Envelope encryption for secrets at rest (Fernet / AES-128-CBC + HMAC).

The TOTP shared secret (and, optionally, other low-volume sensitive fields)
is encrypted at rest with a key derived from the ``SECRETS_ENCRYPTION_KEY``
environment variable. The model is intentionally simple and transparent:

  * ``encrypt(plaintext)`` returns ``"enc:v1:<fernet-token>"`` when a key is
    configured, else the plaintext unchanged.
  * ``decrypt(value)`` reverses it: values carrying the ``enc:v1:`` prefix are
    decrypted; anything else (legacy plaintext) is returned as-is.

This dual behaviour makes adoption a no-op migration: existing plaintext rows
keep working and are re-encrypted lazily the next time the app writes them.
When no key is set (dev / demo), encryption is a transparent pass-through so
nothing breaks — the value is simply stored in the clear, exactly as before.

Key handling: ``SECRETS_ENCRYPTION_KEY`` may be **any** string (a passphrase,
a generated token, a base64 Fernet key). We derive a stable 32-byte Fernet
key from it with SHA-256, so operators don't have to produce a precisely
formatted key. For real KMS integration, set ``SECRETS_ENCRYPTION_KEY`` from
a KMS-decrypted data key at boot — the derivation here is the last mile.

Failure policy: a decrypt failure (wrong/rotated key, corrupt value) raises
``DecryptionError`` — we must NOT silently treat a ciphertext as plaintext,
which would leak gibberish into a TOTP verify. Encryption never raises for the
caller's data; if the backend is somehow unavailable it falls back to
pass-through and logs once.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
from functools import lru_cache
from typing import Optional

logger = logging.getLogger(__name__)

_PREFIX = "enc:v1:"
_KEY_ENV = "SECRETS_ENCRYPTION_KEY"


class DecryptionError(ValueError):
    """An ``enc:v1:`` value could not be decrypted (wrong key / corruption)."""


def _derive_fernet_key(passphrase: str) -> bytes:
    """Derive a urlsafe-base64 32-byte Fernet key from an arbitrary string."""
    digest = hashlib.sha256(passphrase.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


@lru_cache(maxsize=1)
def _get_fernet():
    """Return a cached Fernet instance, or None when encryption is unconfigured
    or the backend is unavailable."""
    passphrase = os.getenv(_KEY_ENV)
    if not passphrase:
        return None
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        logger.warning(
            "%s is set but the 'cryptography' package is unavailable; "
            "secrets will be stored in plaintext.", _KEY_ENV
        )
        return None
    return Fernet(_derive_fernet_key(passphrase))


def reset_cache_for_testing() -> None:
    """Drop the cached Fernet so a test can flip ``SECRETS_ENCRYPTION_KEY``."""
    _get_fernet.cache_clear()


def encryption_enabled() -> bool:
    """Whether a usable encryption backend is configured."""
    return _get_fernet() is not None


def is_encrypted(value: Optional[str]) -> bool:
    """Whether ``value`` is an envelope ciphertext produced by ``encrypt``."""
    return value is not None and value.startswith(_PREFIX)


def encrypt(plaintext: str) -> str:
    """Encrypt ``plaintext`` to an ``enc:v1:`` token, or pass through unchanged
    when encryption is unconfigured. Already-encrypted input is returned as-is
    (idempotent)."""
    if plaintext is None:
        return plaintext
    if is_encrypted(plaintext):
        return plaintext
    fernet = _get_fernet()
    if fernet is None:
        return plaintext
    token = fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")
    return f"{_PREFIX}{token}"


def decrypt(value: str) -> str:
    """Decrypt an ``enc:v1:`` value; return non-prefixed (legacy plaintext)
    values unchanged. Raises ``DecryptionError`` if a prefixed value can't be
    decrypted."""
    if not is_encrypted(value):
        return value
    fernet = _get_fernet()
    if fernet is None:
        raise DecryptionError(
            f"value is encrypted but {_KEY_ENV} is not configured"
        )
    token = value[len(_PREFIX):]
    try:
        from cryptography.fernet import InvalidToken
        return fernet.decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise DecryptionError("could not decrypt value (wrong or rotated key)") from exc
