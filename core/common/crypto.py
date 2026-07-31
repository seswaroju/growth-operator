"""Symmetric encryption for credentials at rest (MVP-031).

Channel credentials (a WABA access token) are encrypted with Fernet (AES-128-CBC + HMAC)
using `Settings.credential_encryption_key` before they touch the database, and decrypted
only when the send adapter needs them. The key comes from SOPS in production; a deterministic
dev key is used locally. Ciphertext is what's stored; plaintext never is, and never logged.
"""

from __future__ import annotations

import json
from typing import Any

from cryptography.fernet import Fernet

from core.common.config import get_settings


def _fernet() -> Fernet:
    return Fernet(get_settings().credential_encryption_key.encode())


def encrypt_json(data: dict[str, Any]) -> str:
    """Encrypt a JSON-serialisable dict to a urlsafe token string."""
    return _fernet().encrypt(json.dumps(data).encode()).decode()


def decrypt_json(token: str) -> dict[str, Any]:
    """Decrypt a token produced by `encrypt_json` back to a dict."""
    return json.loads(_fernet().decrypt(token.encode()).decode())
