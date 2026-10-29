"""Stripe webhook signature verification.

Implements Stripe's documented scheme without the SDK:
header `Stripe-Signature: t=<unix>,v1=<hex hmac>[,v1=...]`, where the HMAC-SHA256
is computed over "<t>.<raw body>" using the endpoint's signing secret.
"""

import hashlib
import hmac
import time


class SignatureError(Exception):
    pass


def compute_signature(payload: bytes, timestamp: int, secret: str) -> str:
    signed = f"{timestamp}.".encode() + payload
    return hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()


def build_header(payload: bytes, secret: str, timestamp: int | None = None) -> str:
    """Create a valid header; used by tests and the local test-event script."""
    ts = int(time.time()) if timestamp is None else timestamp
    return f"t={ts},v1={compute_signature(payload, ts, secret)}"


def verify(payload: bytes, header: str | None, secret: str, tolerance: int, now: float | None = None) -> None:
    if not header:
        raise SignatureError("missing Stripe-Signature header")

    timestamp = None
    candidates = []
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            timestamp = value
        elif key == "v1":
            candidates.append(value)

    if timestamp is None or not timestamp.isdigit() or not candidates:
        raise SignatureError("malformed Stripe-Signature header")

    ts = int(timestamp)
    now = time.time() if now is None else now
    if abs(now - ts) > tolerance:
        raise SignatureError("timestamp outside tolerance (possible replay)")

    expected = compute_signature(payload, ts, secret)
    if not any(hmac.compare_digest(expected, c) for c in candidates):
        raise SignatureError("signature mismatch")
