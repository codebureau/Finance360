"""Fund-of-funds references on an Exposure profile against a real migrated temporary SQLite
(integration): stored exactly, audited, judged with the tolerance, loops and depth rejected with a
message naming them, corrections superseding, and the database's own guards (ADR-0030 section 5).
"""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DatabaseError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from finance360.capabilities.lookthrough.lines import Part, compose
from finance360.capabilities.reference import exposure_profiles as svc
from finance360.capabilities.reference import instruments as inst_svc
from finance360.capabilities.reference.exposure_profiles import (
    ProfileInput,
    ReferenceInput,
    WeightInput,
)
from finance360.capabilities.reference.instruments import InstrumentInput
from finance360.core.audit import AuditContext
from finance360.core.errors import ValidationError
from finance360.core.exposure import MAX_DEPTH
from finance360.core.exposure_vocab import seed_exposure_vocab
from finance360.core.models.audit import AuditEvent
from finance360.core.models.exposure import ExposureReference
from finance360.core.money import Money

CTX = AuditContext("owner", "owner", "api")
REPO = Path(__file__).resolve().parents[2]
AT = date(2026, 6, 30)
TODAY = date(2026, 11, 23)


def _cash(percent: str = "100") -> WeightInput:
    return WeightInput("asset_class", "cash", percent)


def _in(
    *references: tuple[int, str],
    weights: tuple[WeightInput, ...] = (),
    as_at: date = AT,
) -> ProfileInput:
    return ProfileInput(
        as_at=as_at,
        weights=weights,
        provenance_note="Invented fact sheet",
        retrieved_on=TODAY,
        references=tuple(ReferenceInput(i, p) for i, p in references),
    )


def _errors(exc: pytest.ExceptionInfo[ValidationError]) -> dict[str, str]:
    return {e.field: e.message for e in exc.value.errors}


@pytest.fixture
def funds(session_factory: sessionmaker[Session]) -> dict[str, int]:
    """Eight invented funds, A to H, with no profiles yet."""
    with session_factory.begin() as s:
        seed_exposure_vocab(s)
        return {
            n: inst_svc.create_instrument(s, CTX, InstrumentInput(f"Fund {n}", "fund", "AUD")).id
            for n in "ABCDEFGH"
        }


def _write(factory: sessionmaker[Session], instrument_id: int, data: ProfileInput) -> None:
    with factory.begin() as s:
        svc.record_profile(s, CTX, instrument_id, data)


def _reject(
    factory: sessionmaker[Session], instrument_id: int, data: ProfileInput
) -> dict[str, str]:
    with pytest.raises(ValidationError) as caught, factory.begin() as s:
        svc.record_profile(s, CTX, instrument_id, data)
    return _errors(caught)


def _profile_count(factory: sessionmaker[Session], instrument_id: int) -> int:
    with factory() as s:
        return len(svc.list_profiles(s, instrument_id))


def test_a_fund_of_funds_profile_stores_its_references_exactly_and_is_audited(
    session_factory: sessionmaker[Session], funds: dict[str, int]
) -> None:
    _write(
        session_factory,
        funds["A"],
        _in((funds["B"], "55.5"), (funds["C"], "34.5"), weights=(_cash("10"),)),
    )
    with session_factory() as s:
        [profile] = svc.list_profiles(s, funds["A"])
        assert [(r.instrument_id, r.weight_ppm) for r in profile.references] == [
            (funds["B"], 555_000),
            (funds["C"], 345_000),
        ]
        [event] = s.scalars(
            select(AuditEvent).where(AuditEvent.entity_type == "exposure_profile")
        ).all()
    assert event.after is not None
    assert event.after["references"] == [
        {"instrument_id": funds["B"], "percent": "55.5"},
        {"instrument_id": funds["C"], "percent": "34.5"},
    ]


def test_a_profile_may_be_references_alone_and_an_underlying_fund_needs_no_profile_yet(
    session_factory: sessionmaker[Session], funds: dict[str, int]
) -> None:
    _write(session_factory, funds["A"], _in((funds["B"], "60"), (funds["C"], "40")))
    assert _profile_count(session_factory, funds["A"]) == 1
    assert _profile_count(session_factory, funds["B"]) == 0  # B has no profile: it is No profile


def test_nothing_at_all_is_rejected(
    session_factory: sessionmaker[Session], funds: dict[str, int]
) -> None:
    errors = _reject(session_factory, funds["A"], _in())
    assert errors["weights"] == "Enter at least one weight or underlying fund."


def test_a_fund_cannot_hold_itself(
    session_factory: sessionmaker[Session], funds: dict[str, int]
) -> None:
    errors = _reject(session_factory, funds["A"], _in((funds["A"], "100")))
    assert "Fund A > Fund A" in errors["references"]
    assert _profile_count(session_factory, funds["A"]) == 0


def test_a_loop_through_other_funds_is_rejected_naming_it_and_nothing_is_written(
    session_factory: sessionmaker[Session], funds: dict[str, int]
) -> None:
    _write(session_factory, funds["A"], _in((funds["B"], "100")))
    _write(session_factory, funds["B"], _in((funds["C"], "100")))
    errors = _reject(session_factory, funds["C"], _in((funds["A"], "100")))
    assert "Fund C > Fund A > Fund B > Fund C" in errors["references"]
    assert _profile_count(session_factory, funds["C"]) == 0
    with session_factory() as s:  # the references that exist are untouched
        assert len(s.scalars(select(ExposureReference)).all()) == 2


def test_a_correction_is_not_judged_against_the_profile_it_replaces(
    session_factory: sessionmaker[Session], funds: dict[str, int]
) -> None:
    _write(session_factory, funds["A"], _in((funds["B"], "100")))
    # B > A would loop through A's current profile...
    assert (
        "Fund B > Fund A > Fund B"
        in _reject(session_factory, funds["B"], _in((funds["A"], "100")))["references"]
    )
    # ...but correcting A at the same date to hold C instead removes that edge, so B > A is fine
    _write(session_factory, funds["A"], _in((funds["C"], "100")))
    _write(session_factory, funds["B"], _in((funds["A"], "100")))
    assert _profile_count(session_factory, funds["A"]) == 2  # the earlier version is kept


def test_references_may_nest_to_the_cap_and_no_deeper(
    session_factory: sessionmaker[Session], funds: dict[str, int]
) -> None:
    chain = [funds[n] for n in "ABCDEFGH"]  # A > B > ... : MAX_DEPTH references is allowed
    for held, under in zip(chain[:MAX_DEPTH], chain[1 : MAX_DEPTH + 1], strict=True):
        _write(session_factory, held, _in((under, "100")))
    too_deep = _reject(session_factory, chain[MAX_DEPTH], _in((chain[MAX_DEPTH + 1], "100")))
    assert f"{MAX_DEPTH + 1} levels deep" in too_deep["references"]
    # writing at the top of a chain is judged against what hangs below it, too
    assert (
        "levels deep"
        in _reject(session_factory, chain[MAX_DEPTH + 2], _in((chain[0], "100")))["references"]
    )


def test_each_reference_is_checked_field_by_field(
    session_factory: sessionmaker[Session], funds: dict[str, int]
) -> None:
    errors = _reject(
        session_factory,
        funds["A"],
        _in((9999, "50"), (funds["B"], "abc"), (funds["C"], "10"), (funds["C"], "10")),
    )
    assert errors[f"references.{9999}"] == "This Instrument does not exist."
    assert errors[f"references.{funds['B']}"].startswith("Enter a percentage")
    assert errors[f"references.{funds['C']}"] == "This Instrument is entered twice."


def test_references_totalling_over_the_tolerance_are_rejected(
    session_factory: sessionmaker[Session], funds: dict[str, int]
) -> None:
    errors = _reject(session_factory, funds["A"], _in((funds["B"], "60"), (funds["C"], "41.5")))
    assert "total 101.5%" in errors["references"]
    _write(session_factory, funds["A"], _in((funds["B"], "60"), (funds["C"], "41")))  # 101: fine


def test_references_count_towards_a_dimensions_total(
    session_factory: sessionmaker[Session], funds: dict[str, int]
) -> None:
    # 60% in a fund + 30% cash is 90%: beyond the tolerance, and the message says references count
    errors = _reject(session_factory, funds["A"], _in((funds["B"], "60"), weights=(_cash("30"),)))
    assert "weights and underlying funds total 90%" in errors["weights"]
    _write(session_factory, funds["A"], _in((funds["B"], "60"), weights=(_cash("39.5"),)))
    # a Dimension the profile gives no weights is not judged: the rest is simply Not classified
    _write(
        session_factory,
        funds["C"],
        _in((funds["B"], "60"), weights=(WeightInput("region", "australia", "40"),)),
    )


def test_a_correction_that_changes_only_a_reference_supersedes_and_the_same_write_changes_nothing(
    session_factory: sessionmaker[Session], funds: dict[str, int]
) -> None:
    first = _in((funds["B"], "100"))
    _write(session_factory, funds["A"], first)
    _write(session_factory, funds["A"], first)  # identical: no new version
    assert _profile_count(session_factory, funds["A"]) == 1
    _write(session_factory, funds["A"], _in((funds["C"], "100")))  # a different underlying fund
    with session_factory() as s:
        current, earlier = svc.list_profiles(s, funds["A"])
        assert current.superseded_at is None and earlier.superseded_at is not None
        assert [r.instrument_id for r in current.references] == [funds["C"]]
        assert [r.instrument_id for r in earlier.references] == [funds["B"]]  # kept, untouched


def test_the_database_refuses_to_change_a_reference_or_store_a_float(
    session_factory: sessionmaker[Session], funds: dict[str, int]
) -> None:
    _write(session_factory, funds["A"], _in((funds["B"], "100")))
    with session_factory() as s:
        with pytest.raises(DatabaseError, match="immutable"):
            s.execute(text("UPDATE exposure_reference SET weight_ppm = 1"))
    with session_factory() as s:
        with pytest.raises(DatabaseError, match="immutable"):
            s.execute(text("DELETE FROM exposure_reference"))
    with session_factory() as s:
        with pytest.raises(DatabaseError, match="must be an integer"):
            s.execute(
                text(
                    "INSERT INTO exposure_reference (profile_id, instrument_id, weight_ppm) "
                    "VALUES (1, 1, 500000.5)"
                )
            )
    with session_factory() as s:
        with pytest.raises(IntegrityError):
            s.execute(
                text(
                    "INSERT INTO exposure_reference (profile_id, instrument_id, weight_ppm) "
                    "VALUES (1, 1, 0)"
                )
            )


def test_the_fixture_fund_of_funds_loads_resolves_and_reconciles_through_the_database(
    session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    shutil.copy(
        REPO / "fixtures" / "sample_household" / "household.json", tmp_path / "household.json"
    )
    with session_factory.begin() as s:
        seed_exposure_vocab(s)
        inst_svc.load_fixture_instruments(s, tmp_path)
        assert svc.load_fixture_exposure_profiles(s, tmp_path) == 7
    with session_factory() as s:
        ids = {i.name: i.id for i in inst_svc.list_instruments(s)}
        fund = ids["Sample Diversified Fund"]
        [profile] = svc.list_profiles(s, fund)
        assert [(r.instrument_id, r.weight_ppm) for r in profile.references] == [
            (ids["Sample Broad Market ETF"], 550_000),
            (ids["Sample Global Equity ETF"], 350_000),
        ]
        profiles = svc.profile_facts_closure(s, [fund])
        assert set(profiles) == {
            fund,
            ids["Sample Broad Market ETF"],
            ids["Sample Global Equity ETF"],
        }
        names = svc.instrument_names(s, set(profiles))
        labels = [(k, k) for k in svc.label_order(s, "asset_class")]
        got = compose(
            position_value=Money(152_000, "AUD"),
            parts_value=Money(152_000, "AUD"),
            parts=[Part(fund, "Sample Diversified Fund", Money(152_000, "AUD"))],
            dimension="asset_class",
            labels=labels,
            on=date(2026, 9, 30),
            profiles=profiles,
            names_of=names,
        )
    assert {line.key: line.amount.amount_minor for line in got.lines if line.kind == "label"} == {
        "australian_shares": 50_160,
        "international_shares": 85_690,
        "cash": 15_732,
    }
    assert sum(line.amount.amount_minor for line in got.lines) == 152_000
