"""Tests for PHI-at-rest envelope encryption."""
from __future__ import annotations

import base64
import importlib
import os

import pytest

from security import phi_crypto


@pytest.fixture
def key_env(monkeypatch):
    key = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("TRIAGE_PHI_KEK", key)
    monkeypatch.setenv("TRIAGE_PHI_ACTIVE_KID", "k1")
    monkeypatch.delenv("TRIAGE_PHI_KEK_OLD", raising=False)
    return key


def test_bytes_round_trip(key_env):
    pt = b"ISA*00* ... raw 837 with SSN 123-45-6789 ~"
    token = phi_crypto.seal_bytes(pt)
    assert token.startswith(b"PHI1")
    assert pt not in token  # ciphertext does not contain the plaintext
    assert phi_crypto.open_bytes(token) == pt


def test_text_round_trip(key_env):
    pt = "raw excerpt with DOB 1980-01-01 and dx Z000"
    token = phi_crypto.seal_text(pt)
    assert token.startswith("PHI1:")
    assert "Z000" not in token
    assert phi_crypto.open_text(token) == pt


def test_legacy_cleartext_reads_through(key_env):
    # Rows written before encryption have no magic prefix -> returned unchanged.
    assert phi_crypto.open_bytes(b"raw cleartext 837") == b"raw cleartext 837"
    assert phi_crypto.open_text("plain excerpt") == "plain excerpt"


def test_tamper_detection(key_env):
    token = bytearray(phi_crypto.seal_bytes(b"sensitive"))
    token[-1] ^= 0x01  # flip a ciphertext bit
    with pytest.raises(ValueError):
        phi_crypto.open_bytes(bytes(token))


def test_key_rotation_decrypts_with_retired_key(monkeypatch):
    old_key = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("TRIAGE_PHI_KEK", old_key)
    monkeypatch.setenv("TRIAGE_PHI_ACTIVE_KID", "old")
    token = phi_crypto.seal_bytes(b"payload")

    # Rotate: new active key, old key retired but still available for decrypt.
    new_key = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("TRIAGE_PHI_KEK", new_key)
    monkeypatch.setenv("TRIAGE_PHI_ACTIVE_KID", "new")
    monkeypatch.setenv("TRIAGE_PHI_KEK_OLD", f"old:{old_key}")
    assert phi_crypto.open_bytes(token) == b"payload"


def test_dev_passthrough_without_key(monkeypatch):
    monkeypatch.delenv("TRIAGE_PHI_KEK", raising=False)
    monkeypatch.delenv("TRIAGE_REQUIRE_SECRETS", raising=False)
    monkeypatch.delenv("TRIAGE_ENV", raising=False)
    monkeypatch.delenv("TRIAGE_REQUIRE_PHI_ENCRYPTION", raising=False)
    assert phi_crypto.seal_bytes(b"x") == b"x"  # cleartext in dev
    assert phi_crypto.seal_text("x") == "x"


def test_prod_requires_key(monkeypatch):
    monkeypatch.delenv("TRIAGE_PHI_KEK", raising=False)
    monkeypatch.setenv("TRIAGE_REQUIRE_PHI_ENCRYPTION", "true")
    with pytest.raises(RuntimeError):
        phi_crypto.seal_bytes(b"x")
    with pytest.raises(RuntimeError):
        phi_crypto.seal_text("x")
