"""PHI-at-rest envelope encryption (transparent seal / open).

The schema stores raw submitter payloads — ``imports.original_content`` (the raw
837), ``imports.raw_claim`` and Claimtrace ``raw_excerpt`` — which under a BAA
cannot sit in Postgres (or its backups) in cleartext once real 837s flow. This
module wraps those values with AES-256-GCM authenticated encryption keyed by a
key-encryption key from the environment.

Design goals:

* **Transparent**: ``open_*`` reads legacy *cleartext* values unchanged (no magic
  prefix → returned as-is), so existing rows and the running stack keep working;
  new writes are encrypted as soon as a key is configured. No big-bang
  migration required.
* **Authenticated**: GCM detects tampering — a modified ciphertext fails to open.
* **Rotatable**: a ``key id`` is stored in the token; ``TRIAGE_PHI_KEK`` is the
  active key and ``TRIAGE_PHI_KEK_OLD`` (comma-separated ``kid:b64key`` pairs)
  holds retired keys for decryption only.
* **Fail-closed in prod**: when secrets are required (``TRIAGE_REQUIRE_SECRETS``
  or ``TRIAGE_ENV=production``) sealing PHI without a key raises rather than
  silently writing cleartext. In dev it warns once and passes through.

Token layout (bytes): ``b"PHI1" | kid_len(1) | kid | nonce(12) | ct+tag``.
Text columns store ``"PHI1:" + base64(token)``.
"""
from __future__ import annotations

import base64
import logging
import os
import threading

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger("security.phi")

_MAGIC = b"PHI1"
_TEXT_PREFIX = "PHI1:"
_NONCE_LEN = 12

_LOCK = threading.Lock()
_WARNED = False


def _require_encryption() -> bool:
    # Decoupled from the shared-secret requirement: PHI-at-rest encryption is its
    # own control, enabled explicitly or implied by a production environment.
    return (
        os.getenv("TRIAGE_ENV", "").strip().lower() in {"prod", "production"}
        or os.getenv("TRIAGE_REQUIRE_PHI_ENCRYPTION", "").strip().lower()
        in {"1", "true", "yes", "on"}
    )


def _load_key(b64: str) -> bytes:
    raw = base64.b64decode(b64.strip())
    if len(raw) not in (16, 24, 32):
        raise ValueError("PHI key must be 16/24/32 bytes (base64-encoded)")
    return raw


def _active() -> tuple[str, bytes] | None:
    """Return ``(kid, key)`` for the active KEK, or ``None`` if unset."""
    kek = os.getenv("TRIAGE_PHI_KEK", "").strip()
    if not kek:
        return None
    kid = os.getenv("TRIAGE_PHI_ACTIVE_KID", "k1").strip() or "k1"
    return kid, _load_key(kek)


def _keyring() -> dict[str, bytes]:
    """All keys available for *decryption*, keyed by kid."""
    ring: dict[str, bytes] = {}
    active = _active()
    if active:
        ring[active[0]] = active[1]
    old = os.getenv("TRIAGE_PHI_KEK_OLD", "").strip()
    for pair in (p for p in old.split(",") if p.strip()):
        kid, _, b64 = pair.partition(":")
        if kid and b64:
            try:
                ring[kid.strip()] = _load_key(b64)
            except ValueError:
                logger.warning("ignoring malformed retired PHI key %r", kid)
    return ring


def _warn_cleartext() -> None:
    global _WARNED
    with _LOCK:
        if not _WARNED:
            logger.warning(
                "TRIAGE_PHI_KEK is not set; PHI payloads are stored in CLEARTEXT "
                "(dev only). Set TRIAGE_PHI_KEK before real claims flow."
            )
            _WARNED = True


def encryption_enabled() -> bool:
    return _active() is not None


def seal_bytes(plaintext: bytes | None) -> bytes | None:
    """Encrypt ``plaintext`` for at-rest storage (BYTEA columns)."""
    if plaintext is None:
        return None
    active = _active()
    if active is None:
        if _require_encryption():
            raise RuntimeError(
                "TRIAGE_PHI_KEK must be set to store PHI when encryption is "
                "required (TRIAGE_REQUIRE_SECRETS / TRIAGE_ENV=production)."
            )
        _warn_cleartext()
        return plaintext
    kid, key = active
    nonce = os.urandom(_NONCE_LEN)
    ct = AESGCM(key).encrypt(nonce, plaintext, None)
    kid_b = kid.encode("utf-8")
    return _MAGIC + bytes([len(kid_b)]) + kid_b + nonce + ct


def open_bytes(token: bytes | None) -> bytes | None:
    """Decrypt a sealed value; legacy cleartext (no magic) is returned as-is."""
    if token is None:
        return None
    if not token.startswith(_MAGIC):
        return token  # back-compat: pre-encryption cleartext row
    kid_len = token[len(_MAGIC)]
    off = len(_MAGIC) + 1
    kid = token[off : off + kid_len].decode("utf-8")
    off += kid_len
    nonce = token[off : off + _NONCE_LEN]
    ct = token[off + _NONCE_LEN :]
    key = _keyring().get(kid)
    if key is None:
        raise KeyError(f"no PHI key available for key id {kid!r}")
    try:
        return AESGCM(key).decrypt(nonce, ct, None)
    except InvalidTag as exc:  # tampering / wrong key
        raise ValueError("PHI ciphertext failed authentication (tampered?)") from exc


def seal_text(plaintext: str | None) -> str | None:
    """Encrypt a text payload (TEXT columns) -> ``"PHI1:"+base64`` or cleartext."""
    if plaintext is None:
        return None
    if not encryption_enabled():
        if _require_encryption():
            raise RuntimeError(
                "TRIAGE_PHI_KEK must be set to store PHI when encryption is required."
            )
        _warn_cleartext()
        return plaintext
    token = seal_bytes(plaintext.encode("utf-8"))
    return _TEXT_PREFIX + base64.b64encode(token).decode("ascii")


def open_text(token: str | None) -> str | None:
    """Decrypt a sealed text payload; legacy cleartext returned unchanged."""
    if token is None:
        return None
    if not token.startswith(_TEXT_PREFIX):
        return token  # back-compat cleartext
    raw = base64.b64decode(token[len(_TEXT_PREFIX) :])
    out = open_bytes(raw)
    return out.decode("utf-8") if out is not None else None
