"""Cryptographic utilities for PII protection at rest.

Provides:
- Deterministic HMAC-SHA256 hashing for indexed phone lookups without exposing plaintext.
- Phone masking (+91******XXXX).
- Authenticated Encrypt-then-MAC (AES-CTR equivalent using HMAC-SHA256 PRF) for encrypting PII fields
  (names, phones) at rest without requiring external binary dependencies.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from solution.config import PII_SECRET_KEY


def hash_phone(canonical_phone: str) -> str:
    """Compute deterministic HMAC-SHA256 hash of canonical phone for O(1) indexed lookup."""
    return hmac.new(
        PII_SECRET_KEY,
        f"phone_hash:{canonical_phone}".encode("utf-8"),
        hashlib.sha256
    ).hexdigest()


def mask_phone(canonical_phone: str) -> str:
    """Mask canonical phone to format '+91******XXXX'."""
    last4 = canonical_phone[-4:] if len(canonical_phone) >= 4 else canonical_phone
    return f"+91******{last4}"


def _derive_keys(master_key: bytes) -> tuple[bytes, bytes]:
    """Derive encryption and MAC keys from master key."""
    enc_key = hmac.new(master_key, b"enc_key_salt", hashlib.sha256).digest()
    mac_key = hmac.new(master_key, b"mac_key_salt", hashlib.sha256).digest()
    return enc_key, mac_key


def _keystream(enc_key: bytes, iv: bytes, length: int) -> bytes:
    """Generate keystream bytes using HMAC-SHA256 counter mode."""
    stream = bytearray()
    counter = 0
    while len(stream) < length:
        block = hmac.new(enc_key, iv + counter.to_bytes(4, "big"), hashlib.sha256).digest()
        stream.extend(block)
        counter += 1
    return bytes(stream[:length])


def encrypt_pii(plaintext: str) -> str:
    """Encrypt a PII string using authenticated CTR keystream + HMAC-SHA256."""
    if not plaintext:
        return ""
    enc_key, mac_key = _derive_keys(PII_SECRET_KEY)
    iv = secrets.token_bytes(16)
    data = plaintext.encode("utf-8")
    ks = _keystream(enc_key, iv, len(data))
    ciphertext = bytes(a ^ b for a, b in zip(data, ks))
    # Encrypt-then-MAC
    tag = hmac.new(mac_key, iv + ciphertext, hashlib.sha256).digest()
    payload = iv + tag + ciphertext
    return base64.b64encode(payload).decode("ascii")


def decrypt_pii(ciphertext_b64: str) -> str:
    """Decrypt and verify an encrypted PII string."""
    if not ciphertext_b64:
        return ""
    try:
        raw = base64.b64decode(ciphertext_b64.encode("ascii"))
        if len(raw) < 16 + 32:
            return ""
        iv = raw[:16]
        tag = raw[16:48]
        ciphertext = raw[48:]
        enc_key, mac_key = _derive_keys(PII_SECRET_KEY)
        expected_tag = hmac.new(mac_key, iv + ciphertext, hashlib.sha256).digest()
        if not hmac.compare_digest(tag, expected_tag):
            raise ValueError("PII integrity check failed: HMAC tag mismatch")
        ks = _keystream(enc_key, iv, len(ciphertext))
        plaintext = bytes(a ^ b for a, b in zip(ciphertext, ks))
        return plaintext.decode("utf-8")
    except Exception:
        return ""
