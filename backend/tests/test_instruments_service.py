"""Instrument and crosswalk services against a real migrated temporary SQLite: validation, the
one-identifier-one-Instrument rule, lookup, audit and the database's own guards (integration)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import select, text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session, sessionmaker

from finance360.capabilities.reference import instruments as svc
from finance360.capabilities.reference.instruments import IdentifierInput, InstrumentInput
from finance360.core.audit import AuditContext
from finance360.core.errors import NotFoundError, ValidationError
from finance360.core.models.audit import AuditEvent
from finance360.core.models.instrument import Instrument, InstrumentIdentifier

CTX = AuditContext("owner", "owner", "api")
REPO = Path(__file__).resolve().parents[2]


def _etf(name: str = "Sample ETF", *ids: tuple[str, str]) -> InstrumentInput:
    return InstrumentInput(name, "etf", "AUD", None, tuple(IdentifierInput(s, v) for s, v in ids))


def _fields(exc: pytest.ExceptionInfo[ValidationError]) -> dict[str, str]:
    return {e.field: e.message for e in exc.value.errors}


def _events(factory: sessionmaker[Session]) -> list[AuditEvent]:
    with factory() as s:
        q = select(AuditEvent).where(AuditEvent.entity_type == "instrument").order_by(AuditEvent.id)
        return list(s.scalars(q))


def test_a_created_instrument_keeps_its_identifiers_and_is_audited(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as s:
        created = svc.create_instrument(
            s, CTX, _etf("  Sample   ETF ", ("ticker", " sbme "), ("name", "Sample Index ETF"))
        )
        instrument_id = created.id
    with session_factory() as s:
        [i] = svc.list_instruments(s)
        assert (i.name, i.kind, i.currency, i.notes) == ("Sample ETF", "etf", "AUD", None)
        assert [(x.scheme, x.value, x.value_norm) for x in i.identifiers] == [
            ("ticker", "sbme", "sbme"),
            ("name", "Sample Index ETF", "sample index etf"),
        ]
    [event] = _events(session_factory)
    assert (event.action, event.entity_id, event.before) == ("create", instrument_id, None)
    assert event.after is not None and event.after["identifiers"] == [
        {"scheme": "ticker", "value": "sbme"},
        {"scheme": "name", "value": "Sample Index ETF"},
    ]


def test_a_duplicate_identifier_on_a_second_instrument_is_rejected_on_create(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as s:
        svc.create_instrument(s, CTX, _etf("First", ("ticker", "SBME")))
    with pytest.raises(ValidationError) as exc, session_factory.begin() as s:
        svc.create_instrument(s, CTX, _etf("Second", ("ticker", "  sbme ")))
    assert "already identifies First" in _fields(exc)["identifiers[0].value"]
    with session_factory() as s:  # nothing was half-created
        assert [i.name for i in svc.list_instruments(s)] == ["First"]
    assert len(_events(session_factory)) == 1


def test_a_duplicate_identifier_is_rejected_when_added_and_audit_is_unchanged(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as s:
        first = svc.create_instrument(s, CTX, _etf("First", ("ticker", "SBME"))).id
        second = svc.create_instrument(s, CTX, _etf("Second", ("ticker", "SMPM"))).id
    with pytest.raises(ValidationError) as exc, session_factory.begin() as s:
        svc.add_identifier(s, CTX, second, IdentifierInput("ticker", "Sbme"))
    assert "already identifies First" in _fields(exc)["value"]
    with session_factory() as s:
        assert [i.value for i in svc.get_instrument(s, second).identifiers] == ["SMPM"]
        assert [i.value for i in svc.get_instrument(s, first).identifiers] == ["SBME"]
    assert [e.action for e in _events(session_factory)] == ["create", "create"]


def test_the_same_value_under_different_schemes_is_allowed(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as s:
        a = svc.create_instrument(s, CTX, _etf("A", ("ticker", "XYZ"))).id
        b = svc.create_instrument(s, CTX, _etf("B", ("name", "xyz"))).id
    with session_factory() as s:
        found = svc.resolve(s, "xyz", "ticker")
        assert not isinstance(found, svc.Ambiguous) and found is not None and found.id == a
        both = svc.resolve(s, "XYZ")
        assert isinstance(both, svc.Ambiguous) and both.schemes == ("name", "ticker")
        named = svc.resolve(s, "XYZ", "name")
        assert not isinstance(named, svc.Ambiguous) and named is not None and named.id == b


def test_the_database_enforces_uniqueness_when_the_service_check_is_bypassed(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as s:
        first = svc.create_instrument(s, CTX, _etf("First", ("ticker", "SBME"))).id
    with pytest.raises(DatabaseError), session_factory.begin() as s:
        s.add(
            InstrumentIdentifier(
                instrument_id=first, scheme="ticker", value="sbme", value_norm="sbme"
            )
        )


def test_a_race_on_the_unique_index_becomes_a_field_error(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two writers pass the pre-check together; the index still says no, as a field error."""
    with session_factory.begin() as s:
        svc.create_instrument(s, CTX, _etf("First", ("ticker", "SBME")))
        second = svc.create_instrument(s, CTX, _etf("Second", ("ticker", "SMPM"))).id
    monkeypatch.setattr(svc, "_taken", lambda *a, **k: None)  # the other writer won after the check
    with pytest.raises(ValidationError) as exc, session_factory.begin() as s:
        svc.add_identifier(s, CTX, second, IdentifierInput("ticker", "sbme"))
    assert "already identifies another Instrument" in _fields(exc)["value"]
    with session_factory() as s:
        assert [i.value for i in svc.get_instrument(s, second).identifiers] == ["SMPM"]


def test_lookup_resolves_a_ticker_isin_and_name_alias_to_one_instrument(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as s:
        etf = svc.create_instrument(
            s,
            CTX,
            _etf(
                "Sample ETF",
                ("ticker", "SBME"),
                ("isin", "AU0000SMPL01"),
                ("name", "Sample Broad  Market ETF"),
            ),
        ).id
        svc.create_instrument(s, CTX, _etf("Other", ("ticker", "SMPM")))
    with session_factory() as s:
        for value in ("sbme", " SBME ", "au0000smpl01", "sample BROAD market etf"):
            found = svc.resolve(s, value)
            assert isinstance(found, Instrument) and found.id == etf, value
        assert svc.resolve(s, "nothing") is None
        assert svc.resolve(s, "SBME", "yahoo") is None


def test_identifiers_are_added_and_removed_with_audit(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as s:
        fund = svc.create_instrument(s, CTX, InstrumentInput("Sample Fund", "fund", "AUD")).id
    with session_factory.begin() as s:
        svc.add_identifier(s, CTX, fund, IdentifierInput("name", "Sample Balanced Growth"))
    with session_factory() as s:
        [ident] = svc.get_instrument(s, fund).identifiers
        ident_id = ident.id
    with session_factory.begin() as s:
        svc.remove_identifier(s, CTX, fund, ident_id)
    with session_factory() as s:
        assert svc.get_instrument(s, fund).identifiers == []
        assert svc.resolve(s, "Sample Balanced Growth") is None
    events = _events(session_factory)
    assert [e.action for e in events] == ["create", "update", "update"]
    assert events[1].before is not None and events[1].before["identifiers"] == []
    assert events[1].after is not None
    assert events[1].after["identifiers"] == [{"scheme": "name", "value": "Sample Balanced Growth"}]
    assert events[2].summary.startswith("Removed name")
    with pytest.raises(NotFoundError), session_factory.begin() as s:
        svc.remove_identifier(s, CTX, fund, ident_id)


def test_editing_an_instrument_is_audited_and_a_no_op_is_not(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as s:
        i = svc.create_instrument(s, CTX, _etf("Sample ETF")).id
    with session_factory.begin() as s:
        svc.update_instrument(s, CTX, i, _etf("Sample ETF"))  # unchanged
        svc.update_instrument(s, CTX, i, InstrumentInput("Sample ETF 2", "share", "usd", " note "))
    with session_factory() as s:
        got = svc.get_instrument(s, i)
        assert (got.name, got.kind, got.currency, got.notes) == (
            "Sample ETF 2",
            "share",
            "USD",
            "note",
        )
    events = _events(session_factory)
    assert [e.action for e in events] == ["create", "update"]
    assert events[1].before is not None and events[1].before["name"] == "Sample ETF"
    assert events[1].after is not None and events[1].after["currency"] == "USD"


@pytest.mark.parametrize(
    ("data", "field"),
    [
        (InstrumentInput("", "etf", "AUD"), "name"),
        (InstrumentInput("x", "bond", "AUD"), "kind"),
        (InstrumentInput("x", "etf", "ZZZ"), "currency"),
        (InstrumentInput("x", "etf", "AUD", "n" * 2001), "notes"),
        (_etf("x", ("isin", "NOT-AN-ISIN")), "identifiers[0].value"),
        (_etf("x", ("ticker", "  ")), "identifiers[0].value"),
        (_etf("x", ("cusip", "123")), "identifiers[0].scheme"),
        (_etf("x", ("ticker", "A"), ("ticker", " a")), "identifiers[1].value"),
    ],
)
def test_invalid_input_is_rejected_with_the_field(
    session_factory: sessionmaker[Session], data: InstrumentInput, field: str
) -> None:
    with pytest.raises(ValidationError) as exc, session_factory.begin() as s:
        svc.create_instrument(s, CTX, data)
    assert field in _fields(exc)
    assert _events(session_factory) == []


def test_missing_instrument_is_not_found(session_factory: sessionmaker[Session]) -> None:
    with pytest.raises(NotFoundError), session_factory() as s:
        svc.get_instrument(s, 99)


def test_an_instrument_cannot_be_deleted(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as s:
        svc.create_instrument(s, CTX, _etf("Sample ETF"))
    with pytest.raises(DatabaseError, match="never deleted"), session_factory.begin() as s:
        s.execute(text("DELETE FROM instrument"))


def test_the_fixture_roster_loads_once_through_the_service(
    session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    shutil.copy(
        REPO / "fixtures" / "sample_household" / "household.json", tmp_path / "household.json"
    )
    with session_factory.begin() as s:
        assert svc.load_fixture_instruments(s, tmp_path) == 4
    with session_factory.begin() as s:
        assert svc.load_fixture_instruments(s, tmp_path) == 0  # only while none exist
    with session_factory() as s:
        roster = {i.name: i for i in svc.list_instruments(s)}
        assert {i.kind for i in roster.values()} == {"etf", "share", "fund"}
        etf = roster["Sample Broad Market ETF"]
        assert {x.scheme for x in etf.identifiers} == {"ticker", "yahoo", "isin", "name"}
        assert "ticker" not in {x.scheme for x in roster["Sample Balanced Growth Fund"].identifiers}
        # the AUD-listed global ETF has no provider symbol, so a Price fetch never asks for it
        assert "yahoo" not in {x.scheme for x in roster["Sample Global Equity ETF"].identifiers}
    assert {e.source for e in _events(session_factory)} == {"fixture"}
    data = json.loads((tmp_path / "household.json").read_text(encoding="utf-8"))
    assert len(data["instruments"]) == 4


_word = st.text(alphabet="abcdefXYZ019", min_size=1, max_size=6)


@settings(
    suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=25, deadline=None
)
@given(_word, st.sampled_from(["ticker", "yahoo", "vendor_id", "name"]), st.sampled_from(["", " "]))
def test_no_spelling_of_an_identifier_can_name_two_instruments(
    session_factory: sessionmaker[Session], word: str, scheme: str, pad: str
) -> None:
    with session_factory.begin() as s:
        s.execute(text("DELETE FROM instrument_identifier"))
        first = svc.create_instrument(s, CTX, _etf("A", (scheme, word))).id
        other = svc.create_instrument(s, CTX, _etf("B")).id
    for spelling in (word.upper(), word.lower(), pad + word + pad):
        with pytest.raises(ValidationError), session_factory.begin() as s:
            svc.add_identifier(s, CTX, other, IdentifierInput(scheme, spelling))
    with session_factory() as s:
        owner = svc.resolve(s, word, scheme)
        assert not isinstance(owner, svc.Ambiguous) and owner is not None
        assert owner.id == first
        assert svc.get_instrument(s, other).identifiers == []
