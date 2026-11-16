"""The ``account`` and ``super_statement`` Link kinds (ADR-0026 section 3).

Built here, in the Capability that owns the records, and registered by ``main.py`` with the
Library's registry. The Library never imports position.

* An **Account** is closed, never deleted (ADR-0009), so its lineage is itself alone.
* A **Super statement** is corrected by superseding it (ADR-0025), so a statement's lineage is the
  whole chain of versions: a corrected statement keeps its documents, and a document's link is
  described by the current statement in the chain. A statement removed (superseded with no
  replacement) shows as removed, its link kept.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from finance360.capabilities.library.registry import Described, LinkKind
from finance360.core.models.account import Account
from finance360.core.models.super_statement import SuperStatement
from finance360.core.rulepack import fy_label, fy_of

ACCOUNT_KEY = "account"
ACCOUNT_TITLE = "Account"
STATEMENT_KEY = "super_statement"
STATEMENT_TITLE = "Super statement"
CANDIDATE_LIMIT = 20


# --- Account -------------------------------------------------------------------------------


def _account_exists(session: Session, account_id: int) -> bool:
    return session.get(Account, account_id) is not None


def _account_lineage(_session: Session, account_id: int) -> Sequence[int]:
    return [account_id]


def _account_describe(session: Session, ids: Sequence[int]) -> dict[int, Described]:
    found = {
        a.id: a.name for a in session.scalars(select(Account).where(Account.id.in_(list(ids))))
    }
    return {
        i: Described(found[i], f"/accounts/{i}", False)
        if i in found
        else Described(f"{ACCOUNT_TITLE} {i}", None, True)
        for i in ids
    }


def _account_candidates(session: Session, text: str) -> Sequence[tuple[int, str]]:
    statement = select(Account).order_by(Account.name, Account.id)
    if text.strip():
        statement = statement.where(Account.name.icontains(text.strip(), autoescape=True))
    return [(a.id, a.name) for a in session.scalars(statement.limit(CANDIDATE_LIMIT))]


ACCOUNT_KIND = LinkKind(
    key=ACCOUNT_KEY,
    title=ACCOUNT_TITLE,
    exists=_account_exists,
    lineage=_account_lineage,
    describe=_account_describe,
    candidates=_account_candidates,
)


# --- Super statement -----------------------------------------------------------------------


def _statement_exists(session: Session, statement_id: int) -> bool:
    return session.get(SuperStatement, statement_id) is not None


def _latest(session: Session, statement_id: int) -> SuperStatement | None:
    """The last version in the chain starting at ``statement_id`` (itself when never corrected)."""
    row = session.get(SuperStatement, statement_id)
    seen: set[int] = set()
    while row is not None and row.superseded_by_id is not None and row.id not in seen:
        seen.add(row.id)
        row = session.get(SuperStatement, row.superseded_by_id) or row
    return row


def _statement_lineage(session: Session, statement_id: int) -> Sequence[int]:
    """Every version of the statement: those it replaced and those that replaced it."""
    chain = [statement_id]
    seen = {statement_id}
    earlier = [statement_id]
    while earlier:  # walk back: rows whose replacement is already in the chain
        found = session.scalars(
            select(SuperStatement.id).where(SuperStatement.superseded_by_id.in_(earlier))
        ).all()
        earlier = [i for i in found if i not in seen]
        seen.update(earlier)
        chain.extend(earlier)
    row = session.get(SuperStatement, statement_id)
    while row is not None and row.superseded_by_id is not None and row.superseded_by_id not in seen:
        seen.add(row.superseded_by_id)
        chain.append(row.superseded_by_id)
        row = session.get(SuperStatement, row.superseded_by_id)
    return chain


def _statement_label(session: Session, row: SuperStatement) -> str:
    account = session.get(Account, row.account_id)
    name = account.name if account is not None else f"Account {row.account_id}"
    return f"{STATEMENT_TITLE} FY {fy_label(fy_of(row.period_end))}, {name}"


def _statement_describe(session: Session, ids: Sequence[int]) -> dict[int, Described]:
    out: dict[int, Described] = {}
    for i in ids:
        row = _latest(session, i)
        if row is None:
            out[i] = Described(f"{STATEMENT_TITLE} {i}", None, True)
        elif row.superseded_at is not None:  # removed: no replacement
            out[i] = Described(_statement_label(session, row), f"/accounts/{row.account_id}", True)
        else:
            route = f"/accounts/{row.account_id}/super-statements/{row.id}"
            out[i] = Described(_statement_label(session, row), route, False)
    return out


def _statement_candidates(session: Session, text: str) -> Sequence[tuple[int, str]]:
    rows = session.scalars(
        select(SuperStatement)
        .join(Account, Account.id == SuperStatement.account_id)
        .where(SuperStatement.superseded_at.is_(None))
        .where(
            Account.name.icontains(text.strip(), autoescape=True)
            if text.strip()
            else Account.id.is_not(None)
        )
        .order_by(SuperStatement.period_end.desc(), SuperStatement.id.desc())
        .limit(CANDIDATE_LIMIT)
    )
    return [(row.id, _statement_label(session, row)) for row in rows]


SUPER_STATEMENT_KIND = LinkKind(
    key=STATEMENT_KEY,
    title=STATEMENT_TITLE,
    exists=_statement_exists,
    lineage=_statement_lineage,
    describe=_statement_describe,
    candidates=_statement_candidates,
)
