"""Household peer authentication (HMAC with the household key)."""

from __future__ import annotations

import hashlib
import hmac
import time

HDR_HOUSEHOLD = "X-FS-Household"
HDR_TIME = "X-FS-Time"
HDR_SIG = "X-FS-Sig"
MAX_SKEW = 120


def _message(method: str, path: str, ts: str, body: bytes) -> bytes:
    digest = hashlib.sha256(body).hexdigest()
    return f"{method.upper()}\n{path}\n{ts}\n{digest}".encode()


def sign(key: str, household_id: str, method: str, path: str, body: bytes = b"") -> dict[str, str]:
    ts = str(int(time.time()))
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
