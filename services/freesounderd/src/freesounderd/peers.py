"""Household peer authentication (HMAC with the household key)."""

from __future__ import annotations

import hashlib
import hmac
import time

HDR_HOUSEHOLD = "X-FS-Household"
HDR_TIME = "X-FS-Time"
HDR_SIG = "X-FS-Sig"
# Sent back on a rejected request so a peer whose clock is off (a Pi has no
# RTC; until NTP syncs after a reflash it can be hours or days out) can sign
# with the receiver's time instead. The time itself is not a secret; replay
# protection stays at MAX_SKEW of the receiver's own clock.
HDR_NOW = "X-FS-Now"
MAX_SKEW = 120


def _message(method: str, path: str, ts: str, body: bytes) -> bytes:
    digest = hashlib.sha256(body).hexdigest()
    return f"{method.upper()}\n{path}\n{ts}\n{digest}".encode()


def sign(
    key: str,
    household_id: str,
    method: str,
    path: str,
    body: bytes = b"",
    *,
    now: float | None = None,
) -> dict[str, str]:
    ts = str(int(time.time() if now is None else now))
    sig = hmac.new(bytes.fromhex(key), _message(method, path, ts, body), hashlib.sha256)
    return {HDR_HOUSEHOLD: household_id, HDR_TIME: ts, HDR_SIG: sig.hexdigest()}


def verify(
    key: str,
    household_id: str,
    method: str,
    path: str,
    body: bytes,
    headers: dict[str, str],
    *,
    now: float | None = None,
) -> bool:
    lower = {k.lower(): v for k, v in headers.items()}
    if lower.get(HDR_HOUSEHOLD.lower()) != household_id:
        return False
    ts = lower.get(HDR_TIME.lower(), "")
    sig = lower.get(HDR_SIG.lower(), "")
    try:
        skew = abs((now or time.time()) - int(ts))
    except ValueError:
        return False
    if skew > MAX_SKEW:
        return False
    expected = hmac.new(bytes.fromhex(key), _message(method, path, ts, body), hashlib.sha256)
    return hmac.compare_digest(expected.hexdigest(), sig)


def token(key: str, purpose: str, value: str) -> str:
    """A capability for one URL (e.g. one library file), derived from the household key."""

    mac = hmac.new(bytes.fromhex(key), f"{purpose}\n{value}".encode(), hashlib.sha256)
    return mac.hexdigest()[:32]


def token_ok(key: str, purpose: str, value: str, given: str) -> bool:
    return hmac.compare_digest(token(key, purpose, value), given or "")
