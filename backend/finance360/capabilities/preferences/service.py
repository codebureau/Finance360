"""Display preferences: read a Login's map and change it (ADR-0031 section 3).

Not audited (presentation, not a fact). Nothing financial can be stored: the key and value shapes
are validated here and the rows per Login are capped, so free text, amounts and names cannot fit.
The frontend owns what a key and a value mean and ignores one it does not recognise.

``put_preferences`` takes the caller's ``Session``, flushes and never commits.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from finance360.core.errors import FieldError, ValidationError
from finance360.core.models.display_preference import DisplayPreference

KEY_PATTERN = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")
VALUE_PATTERN = re.compile(r"^[a-z0-9-]{1,24}$")
KEY_MAX = 64
MAX_PER_LOGIN = 200


def get_preferences(session: Session, login_id: int) -> dict[str, str]:
    rows = session.scalars(
        select(DisplayPreference)
        .where(DisplayPreference.login_id == login_id)
        .order_by(DisplayPreference.key)
    )
    return {row.key: row.value for row in rows}


def put_preferences(
    session: Session, login_id: int, changes: Mapping[str, str | None]
) -> dict[str, str]:
    """Apply a partial map: a value sets the key, ``None`` deletes it. Returns the whole map."""
    errors: list[FieldError] = []
    for key, value in changes.items():
        if len(key) > KEY_MAX or not KEY_PATTERN.fullmatch(key):
            errors.append(FieldError(key, "Not a valid preference key."))
        elif value is not None and not VALUE_PATTERN.fullmatch(value):
            errors.append(FieldError(key, "Not a valid preference value."))
    if errors:
        raise ValidationError(errors)

    now = datetime.now(UTC).replace(tzinfo=None)
    for key, value in changes.items():
        row = session.get(DisplayPreference, (login_id, key))
        if value is None:
            if row is not None:
                session.delete(row)
        elif row is None:
            session.add(DisplayPreference(login_id=login_id, key=key, value=value, updated_at=now))
        else:
            row.value = value
            row.updated_at = now
    session.flush()

    count = session.scalar(select(func.count()).where(DisplayPreference.login_id == login_id)) or 0
    if count > MAX_PER_LOGIN:
        raise ValidationError(
            [FieldError("preferences", f"At most {MAX_PER_LOGIN} preferences can be kept.")]
        )
    return get_preferences(session, login_id)
