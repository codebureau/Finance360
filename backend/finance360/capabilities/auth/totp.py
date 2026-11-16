"""TOTP (RFC 6238: SHA-1, 6 digits, 30 s, +-1 step) with replay protection, via ``pyotp``."""

from __future__ import annotations

import hmac
from calendar import timegm
from datetime import datetime

import pyotp

STEP_SECONDS = 30
WINDOW = 1
ISSUER = "Finance360"


def new_secret() -> str:
    """A fresh 160-bit secret, base32 (32 characters)."""
    return pyotp.random_base32(length=32)


def provisioning_uri(secret: str, account: str = "owner") -> str:
    uri: str = pyotp.TOTP(secret).provisioning_uri(  # pyright: ignore[reportUnknownMemberType]
        name=account, issuer_name=ISSUER
    )
    return uri


def step_of(moment: datetime) -> int:
    """The 30-second time-step containing ``moment`` (naive UTC; never read as local time)."""
    return timegm(moment.utctimetuple()) // STEP_SECONDS


def accepted_step(secret: str, code: str, last_step: int | None, now: datetime) -> int | None:
    """The time-step ``code`` proves, or None if it is wrong or replayed.

    Steps within +-1 of now are accepted, but only if strictly after ``last_step``, so one code
    can never be used twice (and an older code can't follow a newer one).
    """
    code = code.strip().replace(" ", "")
    if len(code) != 6 or not code.isascii() or not code.isdigit():
        return None
    totp = pyotp.TOTP(secret)
    current = step_of(now)
    for step in range(current - WINDOW, current + WINDOW + 1):
        if last_step is not None and step <= last_step:
            continue
        if hmac.compare_digest(totp.at(step * STEP_SECONDS), code):
            return step
    return None
