"""Account identifier with supersession (ADR-0013 section 5a).

A sensitive real value (see ``core.account_identifier``): never in the repo, logs or error text.
Rows are never updated or deleted (triggers in the migration); a change supersedes. Unique on
(kind, normalised value, institution) among current rows. Services: ``capabilities/imports``.
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from finance360.core.account_identifier import INSTITUTION_MAX, KINDS, VALUE_MAX
from finance360.core.db import Base
from finance360.core.models.supersession import SupersessionMixin


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class AccountIdentifier(SupersessionMixin, Base):
    __tablename__ = "account_identifier"
    __table_args__ = (
        CheckConstraint(_in("kind", KINDS), name="kind_known"),
        CheckConstraint("length(trim(value)) > 0", name="value_required"),
        Index(
            "uq_account_identifier_current",
            "kind",
            "value_norm",
            "institution_norm",
            unique=True,
            sqlite_where=text("superseded_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("account.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    #: As entered (whitespace tidied). Show ``mask_value`` of it; reveal only on request.
    value: Mapped[str] = mapped_column(String(VALUE_MAX))
    #: ``normalise_value(value)``: what uniqueness and matching compare.
    value_norm: Mapped[str] = mapped_column(String(VALUE_MAX))
    institution: Mapped[str | None] = mapped_column(String(INSTITUTION_MAX))
    #: ``normalise_institution(institution)``; ``''`` for none, so the unique index sees one value.
    institution_norm: Mapped[str] = mapped_column(String(INSTITUTION_MAX), default="")
