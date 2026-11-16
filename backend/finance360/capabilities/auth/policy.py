"""Pure policy: password rules, Session lifetimes and the lockout schedule (ADR-0010)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

PASSWORD_MIN = 12
PASSWORD_MAX = 128

NORMAL_SESSION = timedelta(hours=12)  # absolute
TRUSTED_IDLE = timedelta(days=90)  # sliding
TRUSTED_CAP = timedelta(days=365)  # absolute ceiling on a trusted Session
TOUCH_INTERVAL = timedelta(minutes=1)

LOCKOUT_THRESHOLD = 5
LOCKOUT_BASE = 60  # seconds
LOCKOUT_CAP = 15 * 60  # seconds


def utcnow() -> datetime:
    """Naive UTC, the way every timestamp is stored."""
    return datetime.now(UTC).replace(tzinfo=None)


def password_problem(password: str) -> str | None:
    """Why ``password`` is unacceptable, or None. Length only: no composition rules."""
    if len(password) < PASSWORD_MIN:
        return f"Use at least {PASSWORD_MIN} characters."
    if len(password) > PASSWORD_MAX:
        return f"Use at most {PASSWORD_MAX} characters."
    return None


def lock_seconds(failed_count: int) -> int:
    """Seconds to lock after ``failed_count`` consecutive failures: 0 below the threshold, then
    1 minute doubling with each further failure, capped at 15 minutes."""
    if failed_count < LOCKOUT_THRESHOLD:
        return 0
    doublings = min(failed_count - LOCKOUT_THRESHOLD, 16)  # the cap bites long before this
    return min(LOCKOUT_BASE * 2**doublings, LOCKOUT_CAP)
