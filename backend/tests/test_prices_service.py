"""Price services against a real migrated temporary SQLite: validation, supersession, audit, the
explicit missing-Price case, source precedence and the database's own guards (integration)."""

from __future__ import annotations

import shutil
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DatabaseError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from finance360.capabilities.reference import instruments as inst_svc
from finance360.capabilities.reference import prices as svc
from finance360.capabilities.reference.instruments import InstrumentInput
from finance360.capabilities.reference.prices import PriceInput
from finance360.core.audit import AuditContext
from finance360.core.errors import NotFoundError, ValidationError
from finance360.core.models.audit import AuditEvent
from finance360.core.models.price import Price
from finance360.core.price import NoPrice, PricePoint

CTX = AuditContext("owner", "owner", "api")
REPO = Path(__file__).resolve().parents[2]


def _in(day: str, value: str, note: str | None = "Checked", **kw: object) -> PriceInput:
    return PriceInput(date.fromisoformat(day), value, note, **kw)  # type: ignore[arg-type]


@pytest.fixture
def etf(session_factory: sessionmaker[Session]) -> int:
    with session_factory.begin() as s:
        return inst_svc.create_instrument(s, CTX, InstrumentInput("Sample ETF", "etf", "AUD")).id


def _fields(exc: pytest.ExceptionInfo[ValidationError]) -> dict[str, str]:
    return {e.field: e.message for e in exc.value.errors}


def _events(factory: sessionmaker[Session]) -> list[AuditEvent]:
    with factory() as s:
        q = select(AuditEvent).where(AuditEvent.entity_type == "price").order_by(AuditEvent.id)
        return list(s.scalars(q))


def test_an_added_price_is_kept_exactly_and_audited(
    session_factory: sessionmaker[Session], etf: int
) -> None:
    with session_factory.begin() as s:
        svc.add_price(s, CTX, etf, _in("2026-09-01", "98.123456", currency="aud"))
    with session_factory() as s:
        [p] = svc.list_prices(s, etf)
        assert (p.as_at, p.ccy, p.source, p.superseded_at) == (
            date(2026, 9, 1),
            "AUD",
            "manual",
            None,
        )
        assert p.value == Decimal("98.123456") and isinstance(p.value, Decimal)
        assert p.provenance_note == "Checked"
    [event] = _events(session_factory)
    assert (event.action, event.before) == ("create", None)
    assert event.after == {
        "id": p.id,
        "instrument_id": etf,
        "as_at": "2026-09-01",
        "value": "98.123456",
        "ccy": "AUD",
        "source": "manual",
        "provenance_note": "Checked",
    }


def test_a_price_in_another_currency_is_rejected(
    session_factory: sessionmaker[Session], etf: int
) -> None:
    with pytest.raises(ValidationError) as exc, session_factory.begin() as s:
        svc.add_price(s, CTX, etf, _in("2026-09-01", "10", currency="USD"))
    assert "priced in AUD" in _fields(exc)["currency"]
    assert _events(session_factory) == []
    with session_factory() as s:
        assert svc.list_prices(s, etf) == []


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"value": "0"}, "value"),
        ({"value": "-1"}, "value"),
        ({"value": "1.0000001"}, "value"),
        ({"value": "abc"}, "value"),
        ({"value": "1e3"}, "value"),
        ({"note": None}, "provenance_note"),
        ({"note": "   "}, "provenance_note"),
        ({"note": "n" * 2001}, "provenance_note"),
        ({"source": "bogus"}, "source"),
    ],
)
def test_invalid_input_is_rejected_with_the_field(
    session_factory: sessionmaker[Session], etf: int, kwargs: dict[str, object], field: str
) -> None:
    value = str(kwargs.get("value", "10"))
    note = kwargs["note"] if "note" in kwargs else "Checked"
    extra = {"source": kwargs["source"]} if "source" in kwargs else {}
    with pytest.raises(ValidationError) as exc, session_factory.begin() as s:
        svc.add_price(s, CTX, etf, _in("2026-09-01", value, note, **extra))  # type: ignore[arg-type]
    assert field in _fields(exc)
    assert _events(session_factory) == []


def test_one_current_manual_price_per_date(
    session_factory: sessionmaker[Session], etf: int
) -> None:
    with session_factory.begin() as s:
        svc.add_price(s, CTX, etf, _in("2026-09-01", "10"))
    with pytest.raises(ValidationError) as exc, session_factory.begin() as s:
        svc.add_price(s, CTX, etf, _in("2026-09-01", "11"))
    assert "Correct that one instead" in _fields(exc)["as_at"]


def test_the_database_enforces_one_current_price_per_source_and_date(
    session_factory: sessionmaker[Session], etf: int
) -> None:
    with session_factory.begin() as s:
        svc.add_price(s, CTX, etf, _in("2026-09-01", "10"))
    insert = text(
        "INSERT INTO price (instrument_id, as_at, value, ccy, source, provenance_note, recorded_at)"
        " VALUES (:i, '2026-09-01', 5, 'AUD', 'manual', 'n', '2026-09-02 00:00:00')"
    )
    with pytest.raises(IntegrityError), session_factory.begin() as s:
        s.execute(insert, {"i": etf})


def test_a_correction_supersedes_and_is_audited_and_the_old_row_stays(
    session_factory: sessionmaker[Session], etf: int
) -> None:
    with session_factory.begin() as s:
        first = svc.add_price(s, CTX, etf, _in("2026-09-01", "10.5")).id
    with session_factory.begin() as s:
        new = svc.correct_price(s, CTX, etf, first, _in("2026-09-01", "10.75", "Rechecked"))
        new_id = new.id
    with session_factory() as s:
        old = svc.get_price(s, etf, first)
        assert old.superseded_at is not None and old.superseded_by_id == new_id
        assert old.value == Decimal("10.5")  # the earlier record stays retrievable
        found = svc.price_for(s, etf, date(2026, 9, 1))
        assert isinstance(found, PricePoint) and found.value == Decimal("10.75")
        assert [p.id for p in svc.current_prices(s, etf)] == [new_id]
    events = _events(session_factory)
    assert [e.action for e in events] == ["create", "supersede"]
    assert events[1].before is not None and events[1].before["value"] == "10.50"
    assert events[1].after is not None and events[1].after["value"] == "10.75"
    assert events[1].summary.endswith("(was 10.50)")


def test_an_unchanged_correction_writes_nothing(
    session_factory: sessionmaker[Session], etf: int
) -> None:
    with session_factory.begin() as s:
        first = svc.add_price(s, CTX, etf, _in("2026-09-01", "10")).id
    with session_factory.begin() as s:
        assert svc.correct_price(s, CTX, etf, first, _in("2026-09-01", "10.00")).id == first
    assert len(_events(session_factory)) == 1


def test_removal_supersedes_with_no_replacement_and_the_lookup_forgets_it(
    session_factory: sessionmaker[Session], etf: int
) -> None:
    with session_factory.begin() as s:
        a = svc.add_price(s, CTX, etf, _in("2026-09-01", "10")).id
        svc.add_price(s, CTX, etf, _in("2026-09-03", "12"))
    with session_factory.begin() as s:
        svc.remove_price(s, CTX, etf, a)
    with session_factory() as s:
        removed = svc.get_price(s, etf, a)
        assert removed.superseded_at is not None and removed.superseded_by_id is None
        assert svc.price_for(s, etf, date(2026, 9, 2)) == NoPrice(etf, date(2026, 9, 2))
    assert [e.action for e in _events(session_factory)] == ["create", "create", "supersede"]
    with pytest.raises(ValidationError), session_factory.begin() as s:
        svc.remove_price(s, CTX, etf, a)  # only a current Price can be removed


def test_a_manual_price_wins_a_same_date_tie_over_a_provider_price(
    session_factory: sessionmaker[Session], etf: int
) -> None:
    with session_factory.begin() as s:
        svc.add_price(s, CTX, etf, _in("2026-09-01", "10", "Symbol SBME.AX", source="yfinance"))
        svc.add_price(s, CTX, etf, _in("2026-09-01", "10.30", "Owner override"))
    with session_factory() as s:
        found = svc.price_for(s, etf, date(2026, 9, 1))
        assert isinstance(found, PricePoint)
        assert (found.source, found.value) == ("manual", Decimal("10.30"))
        # and the provider's row is still current underneath
        assert {p.source for p in svc.current_prices(s, etf)} == {"manual", "yfinance"}


def test_a_provider_price_cannot_be_corrected_or_removed_here(
    session_factory: sessionmaker[Session], etf: int
) -> None:
    with session_factory.begin() as s:
        feed = svc.add_price(s, CTX, etf, _in("2026-09-01", "10", "Symbol", source="yfinance")).id
    with pytest.raises(ValidationError) as exc, session_factory.begin() as s:
        svc.correct_price(s, CTX, etf, feed, _in("2026-09-01", "11"))
    assert "add a manual one" in _fields(exc)["id"]
    with pytest.raises(ValidationError), session_factory.begin() as s:
        svc.remove_price(s, CTX, etf, feed)


def test_lookup_on_a_gap_before_the_first_price_and_in_the_future(
    session_factory: sessionmaker[Session], etf: int
) -> None:
    with session_factory.begin() as s:
        for day, value in (("2026-09-01", "10"), ("2026-09-02", "11"), ("2026-09-08", "12")):
            svc.add_price(s, CTX, etf, _in(day, value))
    with session_factory() as s:
        gap = svc.price_for(s, etf, date(2026, 9, 5))
        assert isinstance(gap, PricePoint) and gap.as_at == date(2026, 9, 2)
        assert svc.price_for(s, etf, date(2026, 8, 31)) == NoPrice(etf, date(2026, 8, 31))
        future = svc.price_for(s, etf, date(2030, 1, 1))
        assert isinstance(future, PricePoint) and future.as_at == date(2026, 9, 8)


def test_unknown_instrument_or_price_is_not_found(
    session_factory: sessionmaker[Session], etf: int
) -> None:
    with pytest.raises(NotFoundError), session_factory.begin() as s:
        svc.add_price(s, CTX, 999, _in("2026-09-01", "10"))
    with pytest.raises(NotFoundError), session_factory() as s:
        svc.price_for(s, 999, date(2026, 9, 1))
    with pytest.raises(NotFoundError), session_factory() as s:
        svc.get_price(s, etf, 999)


def test_a_price_of_another_instrument_is_not_found_under_this_one(
    session_factory: sessionmaker[Session], etf: int
) -> None:
    with session_factory.begin() as s:
        other = inst_svc.create_instrument(s, CTX, InstrumentInput("Other", "share", "AUD")).id
        pid = svc.add_price(s, CTX, other, _in("2026-09-01", "10")).id
    with pytest.raises(NotFoundError), session_factory.begin() as s:
        svc.remove_price(s, CTX, etf, pid)


def test_the_currency_cannot_change_once_prices_exist(
    session_factory: sessionmaker[Session], etf: int
) -> None:
    with session_factory.begin() as s:
        svc.add_price(s, CTX, etf, _in("2026-09-01", "10"))
    with pytest.raises(ValidationError) as exc, session_factory.begin() as s:
        inst_svc.update_instrument(s, CTX, etf, InstrumentInput("Sample ETF", "etf", "USD"))
    assert "already has Prices in AUD" in _fields(exc)["currency"]
    with session_factory.begin() as s:  # other fields still editable
        inst_svc.update_instrument(s, CTX, etf, InstrumentInput("Sample ETF 2", "etf", "AUD"))


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM price",
        "UPDATE price SET value = 1",
        "UPDATE price SET as_at = '2026-01-01'",
        "UPDATE price SET provenance_note = 'tampered'",
        "UPDATE price SET superseded_by_id = 1",
    ],
)
def test_raw_update_and_delete_are_rejected(
    session_factory: sessionmaker[Session], etf: int, statement: str
) -> None:
    with session_factory.begin() as s:
        svc.add_price(s, CTX, etf, _in("2026-09-01", "10"))
    with pytest.raises(DatabaseError, match="append-only"), session_factory.begin() as s:
        s.execute(text(statement))


def test_a_float_value_cannot_be_stored(session_factory: sessionmaker[Session], etf: int) -> None:
    insert = text(
        "INSERT INTO price (instrument_id, as_at, value, ccy, source, provenance_note, recorded_at)"
        " VALUES (:i, '2026-09-01', :v, 'AUD', 'manual', 'n', '2026-09-02 00:00:00')"
    )
    with pytest.raises(DatabaseError, match="integer"), session_factory.begin() as s:
        s.execute(insert, {"i": etf, "v": 1.5})


def test_the_fixture_prices_load_once_with_gaps_and_provider_and_manual_sources(
    session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    shutil.copy(
        REPO / "fixtures" / "sample_household" / "household.json", tmp_path / "household.json"
    )
    with session_factory.begin() as s:
        inst_svc.load_fixture_instruments(s, tmp_path)
        assert svc.load_fixture_prices(s, tmp_path) > 0
    with session_factory.begin() as s:
        assert svc.load_fixture_prices(s, tmp_path) == 0  # only while none exist
    with session_factory() as s:
        by_kind = {i.kind: i.id for i in inst_svc.list_instruments(s)}
        by_kind["etf"] = next(
            i.id for i in inst_svc.list_instruments(s) if i.name == "Sample Broad Market ETF"
        )
        etf_dates = {p.as_at for p in svc.current_prices(s, by_kind["etf"])}
        gap = date(2026, 8, 19)  # a weekday inside the deliberate week-long gap
        assert gap not in etf_dates
        found = svc.price_for(s, by_kind["etf"], gap)
        assert isinstance(found, PricePoint) and found.as_at == date(2026, 8, 14)
        assert svc.price_for(s, by_kind["etf"], date(2026, 7, 1)) == NoPrice(
            by_kind["etf"], date(2026, 7, 1)
        )
        balanced = next(
            i.id for i in inst_svc.list_instruments(s) if i.name == "Sample Balanced Growth Fund"
        )
        fund = svc.current_prices(s, balanced)
        assert {p.source for p in fund} == {"manual"} and len(fund) == 3
        assert all(p.ccy == "AUD" for p in s.scalars(select(Price)))
    assert {e.source for e in _events(session_factory)} == {"fixture"}
