"""The password provider: a password, plus a TOTP code once the Login has enrolled one.

It only establishes identity (ADR-0010). It owns the failure counter on the Login row, because
that is state about *this* credential; Sessions are issued by ``capabilities.auth.service``.
"""

from __future__ import annotations

from finance360.capabilities.auth import logins, passwords, totp
from finance360.capabilities.auth.service import client_detail
from finance360.capabilities.auth.types import AuthAttempt, Principal, Role


class PasswordProvider:
    key = "password"

    def authenticate(self, req: AuthAttempt) -> Principal | None:
        login = logins.get_login(req.session)
        if login is None:
            return None
        # Counts the attempt up front, under the write lock; raises LockedOut while locked,
        # even for correct credentials. See charge_attempt for why this is not read-then-write.
        locked = logins.charge_attempt(req.session, login, req.now)

        password = req.fields.get("password")
        code = req.fields.get("totp")
        # Always evaluate every factor so a wrong password and a wrong code look identical.
        password_ok = isinstance(password, str) and passwords.verify_password(
            login.password_hash, password
        )
        step: int | None = None
        code_ok = True
        if login.totp_secret is not None:
            step = (
                totp.accepted_step(login.totp_secret, code, login.totp_last_step, req.now)
                if isinstance(code, str)
                else None
            )
            code_ok = step is not None

        if not (password_ok and code_ok):
            logins.record_failure(req.session, login, req.now, locked, client_detail(req))
            return None

        assert isinstance(password, str)  # password_ok implies it  # noqa: S101
        logins.register_success(login)
        if step is not None:
            login.totp_last_step = step
        if passwords.needs_rehash(login.password_hash):
            login.password_hash = passwords.hash_password(password)
        return Principal(login.id, Role(login.role))
