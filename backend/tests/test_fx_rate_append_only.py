"""The ``fx_rate`` table is append-only in the database itself, on real SQLite built
by the real migrations."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session, sessionmaker

from finance360.capabilities.reference import fx_rates as svc
from finance360.capabilities.reference.fx_rates import FxRateInput
from finance360.core.audit import AuditContext

CTX = AuditContext("owner", "owner", "api")

INSERT = (
    "INSERT INTO fx_rate (from_ccy, to_ccy, as_at, rate, source, provenance_note, recorded_at)"
    " VALUES ('GBP', 'AUD', '2026-01-05', {rate}, 'manual', 'n', '2026-01-06 00:00:00')"
)


def _in(rate: str = "2") -> FxRateInput:
    return FxRateInput("GBP", date(2026, 1, 5), rate, "n")


def _add(factory: sessionmaker[Session]) -> int:
    with factory.begin() as s:
        return svc.add_fx_rate(s, CTX, _in()).id


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM fx_rate",
        "UPDATE fx_rate SET rate = 1",
        "UPDATE fx_rate SET from_ccy = 'USD'",
        "UPDATE fx_rate SET as_at = '2026-01-01'",
        "UPDATE fx_rate SET provenance_note = 'tampered'",
        "UPDATE fx_rate SET superseded_by_id = 1",  # linking a replacement on a current row
    ],
)
def test_raw_update_and_delete_are_rejected(
    session_factory: sessionmaker[Session], statement: str
) -> None:
    _add(session_factory)
    with pytest.raises(DatabaseError, match="append-only"), session_factory.begin() as s:
        s.execute(text(statement))
    with session_factory() as s:
        row = s.execute(text("SELECT from_ccy, as_at FROM fx_rate")).one()
    assert tuple(row) == ("GBP", "2026-01-05")


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE fx_rate SET superseded_at = NULL",
        "UPDATE fx_rate SET superseded_at = '2030-01-01 00:00:00'",
        "UPDATE fx_rate SET superseded_by_id = 1",
    ],
)
def test_a_superseded_row_cannot_be_changed_again(
    session_factory: sessionmaker[Session], statement: str
) -> None:
    rid = _add(session_factory)
    with session_factory.begin() as s:
        svc.correct_fx_rate(s, CTX, rid, _in("3"))
    with pytest.raises(DatabaseError, match="append-only"), session_factory.begin() as s:
        s.execute(text(statement + f" WHERE id = {rid}"))


def test_correct_and_remove_through_the_service_still_work(
    session_factory: sessionmaker[Session],
) -> None:
    rid = _add(session_factory)
    with session_factory.begin() as s:
        newer = svc.correct_fx_rate(s, CTX, rid, _in("3")).id
    with session_factory.begin() as s:
        svc.remove_fx_rate(s, CTX, newer)
    with session_factory() as s:
        rows = s.execute(
            text("SELECT id, superseded_by_id, superseded_at IS NOT NULL FROM fx_rate ORDER BY 1")
        ).all()
    assert [tuple(r) for r in rows] == [(rid, newer, 1), (newer, None, 1)]


#: SQLite itself coerces a numeric string such as '12' to an integer before the trigger sees it.
@pytest.mark.parametrize("rate", ["1.5", "'abc'"])
def test_a_non_integer_rate_cannot_be_inserted(
    session_factory: sessionmaker[Session], rate: str
) -> None:
    with pytest.raises(DatabaseError), session_factory.begin() as s:
        s.execute(text(INSERT.format(rate=rate)))
    with session_factory() as s:
        assert s.execute(text("SELECT count(*) FROM fx_rate")).scalar_one() == 0


def test_an_integer_rate_can_be_inserted(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as s:
        s.execute(text(INSERT.format(rate=20_000_000_000)))
    with session_factory() as s:
        assert s.execute(text("SELECT rate FROM fx_rate")).scalar_one() == 20_000_000_000
