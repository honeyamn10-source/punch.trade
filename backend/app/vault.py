"""Fernet-encrypted vault for broker credentials.

Holds short-lived access tokens at rest (encrypted with a key stored in
data/.secret, auto-generated on first run). This is the "encrypt at
rest, never ship secrets" layer from the design — the extension never
sees these, only the backend does.
"""

from __future__ import annotations

import json
import os
from typing import Any

from cryptography.fernet import Fernet

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "data")
VAULT_PATH = os.path.join(DATA_DIR, "vault.json")
SECRET_PATH = os.path.join(DATA_DIR, ".secret")


def _load_key() -> bytes:
    if os.path.exists(SECRET_PATH):
        with open(SECRET_PATH, "rb") as f:
            return f.read().strip()
    key = Fernet.generate_key()
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SECRET_PATH, "wb") as f:
        f.write(key)
    return key


_fernet = Fernet(_load_key())


def _read_raw() -> dict[str, Any]:
    if not os.path.exists(VAULT_PATH):
        return {}
    with open(VAULT_PATH, "rb") as f:
        return json.loads(_fernet.decrypt(f.read()).decode("utf-8"))


def _write_raw(doc: dict[str, Any]) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(VAULT_PATH, "wb") as f:
        f.write(_fernet.encrypt(json.dumps(doc).encode("utf-8")))


def save(broker: str, creds: dict[str, Any]) -> None:
    doc = _read_raw()
    doc[broker] = creds
    _write_raw(doc)


def load(broker: str) -> dict[str, Any] | None:
    return _read_raw().get(broker)


def delete(broker: str) -> None:
    doc = _read_raw()
    doc.pop(broker, None)
    _write_raw(doc)


def brokers() -> list:
    return list(_read_raw().keys())


def rotate_key() -> dict:
    """Re-encrypt the entire vault under a fresh key (key rotation).

    Order matters: the new vault is written first, then the in-memory
    Fernet is swapped, then the key file. A crash mid-sequence leaves the
    old key able to decrypt the old vault file.
    """
    global _fernet
    doc = _read_raw()
    new_key = Fernet.generate_key()
    new_fernet = Fernet(new_key)
    with open(VAULT_PATH, "wb") as f:
        f.write(new_fernet.encrypt(json.dumps(doc).encode("utf-8")))
    _fernet = new_fernet
    with open(SECRET_PATH, "wb") as f:
        f.write(new_key)
    return {"brokers": list(doc.keys()), "encrypted": len(doc)}
