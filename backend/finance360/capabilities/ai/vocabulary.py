"""Load the names the redactor must hide from the core models (ADR-0021 section 3).

The Household, every Member (open or not), every Account (open or closed), every Account
identifier ever recorded (a superseded number is still a real number) and the institutions named on
them. Reads core only; the redaction itself is ``redact.py``.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from finance360.capabilities.ai.redact import ACCOUNT, ENTITY, MEMBER, NUMBER, Vocabulary
from finance360.core.models.account import Account
from finance360.core.models.account_identifier import AccountIdentifier
from finance360.core.models.household import Household, Member

#: Shorter parts of a Member's name ("Al") would mangle ordinary words: only the whole name hides.
MIN_NAME_PART = 3


def load(session: Session) -> Vocabulary:
    entries: list[tuple[str, str]] = []
    entries.extend((ENTITY, n) for n in session.scalars(select(Household.name)))
    for name in session.scalars(select(Member.name)):
        entries.append((MEMBER, name))
        # People are called by a part of their name ("Alex" for "Alex Sample").
        entries.extend((MEMBER, part) for part in name.split() if len(part) >= MIN_NAME_PART)
    entries.extend((ACCOUNT, n) for n in session.scalars(select(Account.name)))
    for value, institution in session.execute(
        select(AccountIdentifier.value, AccountIdentifier.institution)
    ):
        entries.append((NUMBER, value))
        if institution:
            entries.append((ENTITY, institution))
    return Vocabulary(tuple(e for e in entries if e[1] and e[1].strip()))
