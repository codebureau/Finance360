"""Host-side recovery CLI (ADR-0010): ``python -m finance360.auth reset``.

Run it on the host, e.g. ``docker compose exec finance360 python -m finance360.auth reset``.
Shell access to the host is the root of trust (ADR-0007), so this is the only recovery path: it
sets a new password, clears TOTP and the lockout, and revokes every Session. Not imported by the
app; it only calls the auth Capability.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from finance360.capabilities.auth import logins
from finance360.capabilities.auth.policy import utcnow
from finance360.config import Settings
from finance360.core.db import db_path, make_engine, make_session_factory


class ResetError(Exception):
    """A user-facing failure: printed without a traceback."""


def reset_password(data_dir: Path, new_password: str) -> None:
    path = db_path(data_dir)
    if not path.is_file():
        raise ResetError(f"No database at {path}. Start the app once first.")
    engine = make_engine(path)
    try:
        with make_session_factory(engine).begin() as session:
            login = logins.get_login(session)
            if login is None:
                raise ResetError("No Login exists yet: open the app and complete setup instead.")
            try:
                logins.reset_password(
                    session, login, new_password, now=utcnow(), detail={"via": "cli"}
                )
            except logins.PasswordPolicyError as problem:
                raise ResetError(problem.message) from None
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m finance360.auth", description=__doc__)
    parser.add_subparsers(dest="command", required=True).add_parser(
        "reset", help="set a new password, clear TOTP and revoke every Session"
    )
    parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        password = getpass.getpass("New password: ")
        if password != getpass.getpass("Repeat new password: "):
            raise ResetError("The two passwords differ.")
        reset_password(Settings.from_env().data_dir, password)
    except ResetError as err:
        print(f"auth: {err}", file=sys.stderr)
        return 1
    print("Password reset. TOTP is off and every Session has been signed out.")
    return 0
