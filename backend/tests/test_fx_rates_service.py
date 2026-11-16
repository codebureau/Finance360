"""FX rate services against a real migrated temporary SQLite: validation, supersession, audit,
the explicit missing-rate case and the database's own guards (integration)."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from finance360.capabilities.reference import fx_rates as svc
from finance360.capabilities.reference.fx_rates import Converted, FxRateInput
from finance360.core.audit import AuditContext
from finance360.core.errors import NotFoundError, ValidationError
from finance360.core.fx import NoRate
from finance360.core.models.audit import AuditEvent
from finance360.core.models.fx_rate import FxRate
from finance360.core.money import Money

CTX = AuditContext("owner", "owner", "api")


def _in(ccy: str, day: str, rate: str, note: str | None = "Checked") -> FxRateInput:
    return FxRateInput(ccy, date.fromisoformat(day), rate, note)


def _fields(exc: pytest.ExceptionInfo[ValidationError]) -> dict[str, str]:
    return {e.field: e.message for e in exc.value.errors}


def _events(factory: sessionmaker[Session]) -> list[AuditEvent]:
    with factory() as s:
        q = select(AuditEvent).where(AuditEvent.entity_type == "fx_rate").order_by(AuditEvent.id)
        return list(s.scalars(q))


def test_an_added_rate_is_kept_exactly_and_audited(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as s:
        svc.add_fx_rate(s, CTX, _in("gbp", "2026-01-05", "2.0143000001"))
    with session_factory() as s:
        [r] = svc.list_fx_rates(s)
        assert (r.from_ccy, r.to_ccy, r.as_at, r.source) == (
            "GBP",
            "AUD",
            date(2026, 1, 5),
            "manual",
        )
        assert r.rate == Decimal("2.0143000001") and isinstance(r.rate, Decimal)
        assert r.provenance_note == "Checked" and r.superseded_at is None
    [event] = _events(session_factory)
    assert (event.action, event.before) == ("create", None)
    assert event.after == {
        "id": r.id,
        "from_ccy": "GBP",
        "to_ccy": "AUD",
        "as_at": "2026-01-05",
        "rate": "2.0143000001",
        "source": "manual",
        "provenance_note": "Checked",
    }


@pytest.mark.parametrize(
    ("data", "field"),
    [
        (_in("GBP", "2026-01-05", "2.01234567891"), "rate"),
        (_in("GBP", "2026-01-05", "0"), "rate"),
        (_in("GBP", "2026-01-05", "abc"), "rate"),
        (_in("GBP", "2026-01-05", "2", " "), "provenance_note"),
        (_in("XXX", "2026-01-05", "2"), "from_ccy"),
        (_in("AUD", "2026-01-05", "2"), "from_ccy"),
        (FxRateInput("GBP", date(2026, 1, 5), "2", "n", to_ccy="USD"), "to_ccy"),
    ],
)
def test_invalid_input_is_refused_with_a_field_message(
    session_factory: sessionmaker[Session], data: FxRateInput, field: str
) -> None:
    with session_factory.begin() as s, pytest.raises(ValidationError) as exc:
        svc.add_fx_rate(s, CTX, data)
    assert field in _fields(exc)
    assert _events(session_factory) == []


def test_a_second_current_rate_for_the_pair_and_date_is_refused(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as s:
        svc.add_fx_rate(s, CTX, _in("GBP", "2026-01-05", "2"))
    with session_factory.begin() as s, pytest.raises(ValidationError) as exc:
        svc.add_fx_rate(s, CTX, _in("GBP", "2026-01-05", "3"))
    assert "as_at" in _fields(exc)
    with session_factory.begin() as s:
        svc.add_fx_rate(s, CTX, _in("USD", "2026-01-05", "1.5"))  # another pair is fine


def test_correcting_supersedes_and_keeps_the_original(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as s:
        old = svc.add_fx_rate(s, CTX, _in("GBP", "2026-01-05", "2")).id
    with session_factory.begin() as s:
        new = svc.correct_fx_rate(s, CTX, old, _in("GBP", "2026-01-05", "2.5", "Fixed")).id
    with session_factory() as s:
        rows = {r.id: r for r in svc.list_fx_rates(s)}
        assert rows[old].superseded_at is not None and rows[old].superseded_by_id == new
        assert rows[new].superseded_at is None and rows[new].rate == Decimal("2.5")
        assert [r.id for r in svc.current_fx_rates(s)] == [new]
        found = svc.rate_for(s, "GBP", "AUD", date(2026, 2, 1))
        assert not isinstance(found, NoRate) and found.rate == Decimal("2.5")
    [_, event] = _events(session_factory)
    assert event.action == "supersede" and event.before and event.after
    assert event.before["rate"] == "2.00" and event.after["rate"] == "2.50"
    with session_factory.begin() as s, pytest.raises(ValidationError):
        svc.correct_fx_rate(s, CTX, old, _in("GBP", "2026-01-05", "9"))  # no longer current


def test_an_unchanged_correction_writes_nothing(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as s:
        rid = svc.add_fx_rate(s, CTX, _in("GBP", "2026-01-05", "2")).id
    with session_factory.begin() as s:
        assert svc.correct_fx_rate(s, CTX, rid, _in("GBP", "2026-01-05", "2.0")).id == rid
    assert len(_events(session_factory)) == 1


def test_removing_supersedes_with_no_replacement_and_lookup_forgets_it(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as s:
        first = svc.add_fx_rate(s, CTX, _in("GBP", "2026-01-05", "2")).id
        second = svc.add_fx_rate(s, CTX, _in("GBP", "2026-03-05", "3")).id
    with session_factory.begin() as s:
        svc.remove_fx_rate(s, CTX, second)
    with session_factory() as s:
        removed = s.get(FxRate, second)
        assert removed and removed.superseded_at and removed.superseded_by_id is None
        found = svc.rate_for(s, "GBP", "AUD", date(2026, 4, 1))
        assert not isinstance(found, NoRate) and found.id == first
    assert [e.action for e in _events(session_factory)] == ["create", "create", "supersede"]
    with session_factory.begin() as s, pytest.raises(NotFoundError):
        svc.remove_fx_rate(s, CTX, 999)


def test_a_removed_date_can_be_entered_again(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as s:
        rid = svc.add_fx_rate(s, CTX, _in("GBP", "2026-01-05", "2")).id
    with session_factory.begin() as s:
        svc.remove_fx_rate(s, CTX, rid)
        svc.add_fx_rate(s, CTX, _in("GBP", "2026-01-05", "2.1"))


def test_a_missing_rate_is_explicit_in_the_service_api(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as s:
        svc.add_fx_rate(s, CTX, _in("GBP", "2026-01-05", "2"))
    with session_factory() as s:
        before = svc.convert_on(s, Money(10_000, "GBP"), "AUD", date(2026, 1, 4))
        assert before == NoRate("GBP", "AUD", date(2026, 1, 4))
        other = svc.convert_on(s, Money(10_000, "USD"), "AUD", date(2026, 6, 1))
        assert isinstance(other, NoRate)
        inverse = svc.convert_on(s, Money(10_000, "AUD"), "GBP", date(2026, 6, 1))
        assert isinstance(inverse, NoRate)  # inverse pairs are not derived
        with pytest.raises(ValidationError):
            svc.convert_on(s, Money(1, "AUD"), "AUD", date(2026, 6, 1))


def test_conversion_uses_the_latest_rate_on_or_before_the_date(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as s:
        svc.add_fx_rate(s, CTX, _in("GBP", "2026-03-01", "2.1"))  # out of order
        svc.add_fx_rate(s, CTX, _in("GBP", "2026-01-01", "2.0"))
    with session_factory() as s:
        feb = svc.convert_on(s, Money(10_000, "GBP"), "AUD", date(2026, 2, 15))
        assert feb == Converted(
            Money(10_000, "GBP"), Money(20_000, "AUD"), Decimal("2.0"), date(2026, 1, 1)
        )
        mar = svc.convert_on(s, Money(10_000, "GBP"), "AUD", date(2026, 3, 1))
        assert isinstance(mar, Converted) and mar.converted == Money(21_000, "AUD")
        assert mar.rate_as_at == date(2026, 3, 1)


def test_the_database_refuses_what_the_service_would(
    session_factory: sessionmaker[Session],
) -> None:
    sql = (
        "INSERT INTO fx_rate (from_ccy, to_ccy, as_at, rate, source, provenance_note, recorded_at)"
        " VALUES ('GBP', 'AUD', '2026-01-01', :rate, 'manual', :note, '2026-01-01')"
    )
    for params in ({"rate": 0, "note": "n"}, {"rate": 5, "note": " "}):
        with pytest.raises(IntegrityError), session_factory.begin() as s:
            s.execute(text(sql), params)
    with pytest.raises(IntegrityError), session_factory.begin() as s:
        s.execute(text(sql.replace("'AUD'", "'GBP'")), {"rate": 5, "note": "n"})


def test_the_fixture_loads_once_through_the_service(
    session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    item = {"from_ccy": "GBP", "as_at": "2025-01-01", "rate": "2.0143", "note": "x"}
    (tmp_path / "household.json").write_text(json.dumps({"fx_rates": [item]}))
    with session_factory.begin() as s:
        assert svc.load_fixture_fx_rates(s, tmp_path) == 1
        assert svc.load_fixture_fx_rates(s, tmp_path) == 0  # only while there are none
        assert s.scalar(select(func.count()).select_from(FxRate)) == 1
    assert {e.source for e in _events(session_factory)} == {"fixture"}
