"""Password hashing: argon2id (m=64 MiB, t=3, p=1). Parameters live in the PHC string."""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

#: Raise these later: ``needs_rehash`` then upgrades each hash on that Login's next sign-in.
_HASHER = PasswordHasher(memory_cost=64 * 1024, time_cost=3, parallelism=1)


def hash_password(password: str) -> str:
    return _HASHER.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _HASHER.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _HASHER.check_needs_rehash(password_hash)
