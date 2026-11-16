"""Pure auth logic: lockout backoff, password policy, TOTP step replay, origin check, redaction."""

from __future__ import annotations

import base64
import logging
from datetime import datetime, timedelta

import pyotp
import pytest
from hypothesis import given
from hypothesis import strategies as st

from finance360.capabilities.auth import csrf, totp
from finance360.capabilities.auth.policy import (
    LOCKOUT_CAP,
    PASSWORD_MAX,
    PASSWORD_MIN,
    lock_seconds,
    password_problem,
)
from finance360.capabilities.auth.redact import REDACTED, RedactFilter, redact

EPOCH = datetime(1970, 1, 1)


# --- lockout backoff -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("failures", "seconds"),
    [
        (0, 0),
        (1, 0),
        (4, 0),
        (5, 60),
        (6, 120),
        (7, 240),
        (8, 480),
        (9, 900),
        (10, 900),
        (500, 900),
    ],
)
def test_lockout_schedule(failures: int, seconds: int) -> None:
    assert lock_seconds(failures) == seconds


@given(st.integers(min_value=0, max_value=100_000))
def test_lockout_never_exceeds_the_cap_and_never_shrinks(failures: int) -> None:
    assert 0 <= lock_seconds(failures) <= LOCKOUT_CAP
    assert lock_seconds(failures + 1) >= lock_seconds(failures)


@given(st.integers(min_value=5, max_value=12))
def test_each_failure_doubles_the_lock_until_the_cap(failures: int) -> None:
    now, nxt = lock_seconds(failures), lock_seconds(failures + 1)
    assert nxt == min(now * 2, LOCKOUT_CAP)


# --- password policy -------------------------------------------------------------------------


def test_password_policy_is_length_only() -> None:
    assert password_problem("a" * (PASSWORD_MIN - 1)) is not None
    assert password_problem("a" * PASSWORD_MIN) is None  # no composition rules
    assert password_problem("a" * PASSWORD_MAX) is None
    assert password_problem("a" * (PASSWORD_MAX + 1)) is not None


# --- TOTP ------------------------------------------------------------------------------------

#: RFC 6238 appendix B: SHA-1 secret "12345678901234567890", T=59 s -> 94287082 (6 digits: 287082).
RFC_SECRET = base64.b32encode(b"12345678901234567890").decode()


def test_rfc_6238_golden_vector() -> None:
    assert totp.accepted_step(RFC_SECRET, "287082", None, EPOCH + timedelta(seconds=59)) == 1


def test_new_secret_is_160_bits_of_base32() -> None:
    secret = totp.new_secret()
    assert len(secret) == 32  # 32 base32 characters = 160 bits
    assert totp.provisioning_uri(secret).startswith("otpauth://totp/")


def _code(secret: str, step: int) -> str:
    return pyotp.TOTP(secret).at(step * totp.STEP_SECONDS)


secrets_ = st.binary(min_size=20, max_size=20).map(lambda b: base64.b32encode(b).decode())
moments = st.integers(min_value=10**9, max_value=4 * 10**9).map(
    lambda s: EPOCH + timedelta(seconds=s)
)


@given(secrets_, moments)
def test_the_current_code_is_accepted_once_then_refused(secret: str, now: datetime) -> None:
    step = totp.step_of(now)
    code = _code(secret, step)
    assert totp.accepted_step(secret, code, None, now) == step
    assert totp.accepted_step(secret, code, step, now) is None  # replay


@given(secrets_, moments, st.integers(min_value=-2, max_value=2), st.integers(-3, 3))
def test_a_code_is_accepted_iff_within_one_step_and_after_the_last_accepted(
    secret: str, now: datetime, offset: int, last_offset: int
) -> None:
    step = totp.step_of(now) + offset
    last = totp.step_of(now) + last_offset
    expected = step if abs(offset) <= 1 and step > last else None
    assert totp.accepted_step(secret, _code(secret, step), last, now) == expected


@given(secrets_, moments)
def test_an_older_code_cannot_follow_a_newer_one(secret: str, now: datetime) -> None:
    step = totp.step_of(now)
    newer = totp.accepted_step(secret, _code(secret, step), None, now)
    assert newer == step
    assert totp.accepted_step(secret, _code(secret, step - 1), newer, now) is None


@pytest.mark.parametrize("junk", ["", "12345", "1234567", "abcdef", "12 34", "１２３４５６"])
def test_malformed_codes_are_refused(junk: str) -> None:
    assert totp.accepted_step(RFC_SECRET, junk, None, EPOCH + timedelta(seconds=59)) is None


def test_spaces_in_a_pasted_code_are_ignored() -> None:
    assert totp.accepted_step(RFC_SECRET, " 287 082 ", None, EPOCH + timedelta(seconds=59)) == 1


# --- origin check ------------------------------------------------------------------------------


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
def test_safe_methods_need_no_origin(method: str) -> None:
    assert csrf.same_origin(method, {}, "app.example")


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "post"])
def test_unsafe_methods_need_an_origin_matching_the_effective_host(method: str) -> None:
    host = "app.example:8000"
    assert csrf.same_origin(method, {"origin": "http://app.example:8000"}, host)
    assert not csrf.same_origin(method, {"origin": "http://evil.example"}, host)
    assert not csrf.same_origin(method, {"origin": "http://app.example"}, host)  # other port
    assert not csrf.same_origin(method, {}, host)  # neither Origin nor Referer


def test_referer_is_used_when_origin_is_absent() -> None:
    assert csrf.same_origin("POST", {"referer": "https://app.example/settings?x=1"}, "app.example")
    assert not csrf.same_origin(
        "POST", {"referer": "https://evil.example/app.example"}, "app.example"
    )


def test_the_host_is_compared_case_insensitively_and_never_empty() -> None:
    assert csrf.same_origin("POST", {"origin": "https://App.Example"}, "app.EXAMPLE")
    assert not csrf.same_origin("POST", {"origin": "https://app.example"}, "")


# --- log redaction -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "secret"),
    [
        ('{"password": "hunter2-hunter2", "trust_device": true}', "hunter2-hunter2"),
        ("password=hunter2-hunter2&x=1", "hunter2-hunter2"),
        ('{"totp":"123456"}', "123456"),
        ("Cookie: f360_session=abc123_DEF-456; other=1", "abc123_DEF-456"),
        ("{'new_password': 'hunter2-hunter2'}", "hunter2-hunter2"),
        ('{"setup_code": "one-off-code"}', "one-off-code"),
    ],
)
def test_credentials_are_scrubbed_from_text(raw: str, secret: str) -> None:
    out = redact(raw)
    assert secret not in out
    assert REDACTED in out


def test_ordinary_text_is_untouched() -> None:
    line = 'POST /api/household/members HTTP/1.1" 201'
    assert redact(line) == line


def test_the_log_filter_scrubs_formatted_records() -> None:
    record = logging.LogRecord(
        "t", logging.INFO, __file__, 1, "body %s", ('{"password": "hunter2-hunter2"}',), None
    )
    assert RedactFilter().filter(record)
    assert "hunter2" not in record.getMessage()
