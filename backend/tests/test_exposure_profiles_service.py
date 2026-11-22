"""Exposure profile services against a real migrated temporary SQLite (integration): the single
write path, the tolerance rules, supersession that keeps the earlier version, audit, the seeded
vocabulary, and the database's own guards (ADR-0030 sections 2 and 3)."""

from __future__ import annotations

import json
import shutil
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DatabaseError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from finance360.capabilities.reference import exposure_profiles as svc
from finance360.capabilities.reference import instruments as inst_svc
from finance360.capabilities.reference.exposure_profiles import ProfileInput, WeightInput
from finance360.capabilities.reference.instruments import InstrumentInput
from finance360.core.audit import AuditContext
from finance360.core.errors import ValidationError
from finance360.core.exposure_vocab import (
    EXPOSURE_VOCAB_VERSION_KEY,
    seed_exposure_vocab,
)
from finance360.core.models.audit import AuditEvent
from finance360.core.models.catalogue import Meta
from finance360.core.models.exposure import ExposureLabel, ExposureProfile, ExposureWeight

CTX = AuditContext("owner", "owner", "api")
REPO = Path(__file__).resolve().parents[2]
AT = date(2026, 6, 30)
TODAY = date(2026, 11, 22)


def _w(label: str, percent: str, dimension: str = "asset_class") -> WeightInput:
    return WeightInput(dimension, label, percent)


def _in(*weights: WeightInput, as_at: date = AT, note: str | None = "Factsheet") -> ProfileInput:
    return ProfileInput(as_at=as_at, weights=weights, provenance_note=note, retrieved_on=TODAY)


@pytest.fixture
def etf(session_factory: sessionmaker[Session]) -> int:
    with session_factory.begin() as s:
        seed_exposure_vocab(s)
        return inst_svc.create_instrument(s, CTX, InstrumentInput("Sample ETF", "etf", "AUD")).id


def _errors(exc: pytest.ExceptionInfo[ValidationError]) -> dict[str, str]:
    return {e.field: e.message for e in exc.value.errors}


def _events(factory: sessionmaker[Session]) -> list[AuditEvent]:
    with factory() as s:
        query = select(AuditEvent).where(AuditEvent.entity_type == "exposure_profile")
        return list(s.scalars(query.order_by(AuditEvent.id)))


# --- the seeded vocabulary -------------------------------------------------------------------


def test_the_asset_class_vocabulary_is_seeded_once_and_never_overwritten(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as s:
        seed_exposure_vocab(s)
    with session_factory.begin() as s:
        s.execute(text("UPDATE exposure_label SET name = 'Edited' WHERE key = 'cash'"))
        seed_exposure_vocab(s)  # a second run changes nothing
    with session_factory() as s:
        [asset_class] = svc.vocabulary(s)
        assert asset_class.key == "asset_class"
        labels = {label.key: label.name for label in asset_class.labels}
        assert list(labels) == [
            "australian_shares",
            "international_shares",
            "property",
            "infrastructure",
            "fixed_income",
            "cash",
            "alternatives",
            "other",
        ]
        assert labels["cash"] == "Edited"
        assert s.query(ExposureLabel).count() == 8
        assert s.get(Meta, EXPOSURE_VOCAB_VERSION_KEY).value == "1"  # type: ignore[union-attr]


# --- recording -------------------------------------------------------------------------------


def test_a_profile_is_stored_exactly_as_entered_and_audited(
    session_factory: sessionmaker[Session], etf: int
) -> None:
    with session_factory.begin() as s:
        svc.record_profile(
            s, CTX, etf, _in(_w("australian_shares", "60"), _w("international_shares", "39.5"))
        )
    with session_factory() as s:
        [profile] = svc.list_profiles(s, etf)
        assert (profile.as_at, profile.source, profile.age_from, profile.superseded_at) == (
            AT,
            "manual",
            None,
            None,
        )
        assert profile.retrieved_on == TODAY and profile.provenance_note == "Factsheet"
        # exact integer parts per million: 39.5% is 395000, and the 99.5% total is not normalised
        assert [(w.label_key, w.weight_ppm) for w in profile.weights] == [
            ("australian_shares", 600_000),
            ("international_shares", 395_000),
        ]
        assert all(isinstance(w.weight_ppm, int) for w in profile.weights)
    [event] = _events(session_factory)
    assert (event.action, event.before) == ("create", None)
    assert event.after is not None
    assert event.after["weights"] == [
        {"dimension": "asset_class", "label": "australian_shares", "percent": "60"},
        {"dimension": "asset_class", "label": "international_shares", "percent": "39.5"},
    ]


def test_a_correction_supersedes_and_the_earlier_version_stays_retrievable(
    session_factory: sessionmaker[Session], etf: int
) -> None:
    with session_factory.begin() as s:
        first = svc.record_profile(s, CTX, etf, _in(_w("australian_shares", "100")))
        first_id = first.id
    with session_factory.begin() as s:
        second = svc.record_profile(
            s, CTX, etf, _in(_w("australian_shares", "70"), _w("property", "30"))
        )
        second_id = second.id
    assert second_id != first_id
    with session_factory() as s:
        history = svc.list_profiles(s, etf)
        assert [(p.id, p.superseded_by_id) for p in history] == [
            (second_id, None),
            (first_id, second_id),
        ]
        old = svc.get_profile(s, etf, first_id)  # still retrievable, with its own weights
        assert old.superseded_at is not None
        assert [(w.label_key, w.weight_ppm) for w in old.weights] == [
            ("australian_shares", 1_000_000)
        ]
        current = svc.current_profile_facts(s, [etf])[etf]
        assert [p.profile_id for p in current] == [second_id]  # only the current one resolves
    actions = [(e.action, e.entity_id) for e in _events(session_factory)]
    assert actions == [("create", first_id), ("supersede", second_id)]
    supersede = _events(session_factory)[1]
    assert supersede.before is not None and supersede.after is not None
    assert supersede.before["weights"][0]["percent"] == "100"
    assert len(supersede.after["weights"]) == 2


def test_writing_what_is_already_current_changes_nothing(
    session_factory: sessionmaker[Session], etf: int
) -> None:
    with session_factory.begin() as s:
        first = svc.record_profile(s, CTX, etf, _in(_w("cash", "100"))).id
    with session_factory.begin() as s:
        assert svc.record_profile(s, CTX, etf, _in(_w("cash", "100"))).id == first
    assert len(_events(session_factory)) == 1
    with session_factory() as s:
        assert len(svc.list_profiles(s, etf)) == 1


def test_a_different_as_at_is_a_new_profile_not_a_supersession(
    session_factory: sessionmaker[Session], etf: int
) -> None:
    with session_factory.begin() as s:
        svc.record_profile(s, CTX, etf, _in(_w("cash", "100"), as_at=date(2026, 3, 31)))
        svc.record_profile(s, CTX, etf, _in(_w("property", "100")))
    with session_factory() as s:
        history = svc.list_profiles(s, etf)
        assert [(p.as_at, p.superseded_at) for p in history] == [
            (AT, None),
            (date(2026, 3, 31), None),
        ]
        assert [p.as_at for p in svc.current_profile_facts(s, [etf])[etf]] == [
            date(2026, 3, 31),
            AT,
        ]


# --- the tolerance rules at write ------------------------------------------------------------


def _split(total: str) -> tuple[WeightInput, WeightInput]:
    """Two labels totalling ``total`` percent (one weight alone cannot exceed 100%)."""
    rest = Decimal(total) - 60
    return _w("australian_shares", "60"), _w("cash", format(rest, "f"))


@pytest.mark.parametrize("total", ["99", "99.5", "100", "100.5", "101"])
def test_a_total_within_one_point_of_100_is_accepted_as_entered(
    session_factory: sessionmaker[Session], etf: int, total: str
) -> None:
    with session_factory.begin() as s:
        svc.record_profile(s, CTX, etf, _in(*_split(total)))
    with session_factory() as s:
        weights = svc.list_profiles(s, etf)[0].weights
        # stored as entered (never normalised): the total is exactly what was typed
        assert sum(w.weight_ppm for w in weights) == int(Decimal(total) * 10_000)


@pytest.mark.parametrize(
    ("total", "message"),
    [
        ("98.9999", "1.0001 points short of 100%"),
        ("101.0001", "1.0001 points over 100%"),
        ("150", "50 points over 100%"),
        ("70", "30 points short of 100%"),
    ],
)
def test_a_total_beyond_the_tolerance_is_rejected_with_the_difference_shown(
    session_factory: sessionmaker[Session], etf: int, total: str, message: str
) -> None:
    with session_factory.begin() as s, pytest.raises(ValidationError) as caught:
        svc.record_profile(s, CTX, etf, _in(*_split(total)))
    assert message in _errors(caught)["weights"]
    with session_factory() as s:
        assert svc.list_profiles(s, etf) == []  # nothing was written
    assert _events(session_factory) == []


def test_unknown_dimensions_and_labels_are_rejected_and_other_exists(
    session_factory: sessionmaker[Session], etf: int
) -> None:
    with session_factory.begin() as s, pytest.raises(ValidationError) as caught:
        svc.record_profile(
            s,
            CTX,
            etf,
            _in(_w("australian_shares", "50"), _w("galaxies", "30"), _w("cash", "20", "region")),
        )
    errors = _errors(caught)
    assert "not a label" in errors["weights.asset_class.galaxies"]
    assert "Unknown Dimension" in errors["weights.region.cash"]
    with session_factory.begin() as s:  # the manager's own residual is always there
        svc.record_profile(s, CTX, etf, _in(_w("other", "100")))


@pytest.mark.parametrize("bad", ["", "abc", "0", "-1", "100.00001", "12.34567", "1e1"])
def test_a_bad_percentage_is_rejected_field_by_field(
    session_factory: sessionmaker[Session], etf: int, bad: str
) -> None:
    with session_factory.begin() as s, pytest.raises(ValidationError) as caught:
        svc.record_profile(s, CTX, etf, _in(_w("cash", bad)))
    assert "weights.asset_class.cash" in _errors(caught)


def test_a_note_and_a_weight_are_required_and_a_label_cannot_repeat(
    session_factory: sessionmaker[Session], etf: int
) -> None:
    with session_factory.begin() as s, pytest.raises(ValidationError) as caught:
        svc.record_profile(s, CTX, etf, _in(note="  "))
    assert set(_errors(caught)) == {"provenance_note", "weights"}
    with session_factory.begin() as s, pytest.raises(ValidationError) as caught:
        svc.record_profile(s, CTX, etf, _in(_w("cash", "50"), _w("cash", "50")))
    assert "entered twice" in _errors(caught)["weights.asset_class.cash"]


def test_resolutions_state_how_each_total_resolves(
    session_factory: sessionmaker[Session], etf: int
) -> None:
    with session_factory.begin() as s:
        svc.record_profile(s, CTX, etf, _in(_w("australian_shares", "60"), _w("cash", "39.5")))
    with session_factory() as s:
        [r] = svc.resolutions(s, svc.list_profiles(s, etf)[0])
        assert (r.total_ppm, r.difference_ppm, r.not_classified_ppm, r.trimmed) == (
            995_000,
            -5_000,
            5_000,
            (),
        )


# --- the database's own guards ---------------------------------------------------------------


def test_the_database_refuses_to_change_or_delete_what_it_holds(
    session_factory: sessionmaker[Session], etf: int
) -> None:
    with session_factory.begin() as s:
        svc.record_profile(s, CTX, etf, _in(_w("cash", "100")))
    for statement in (
        "UPDATE exposure_profile SET as_at = '2020-01-01'",
        "UPDATE exposure_profile SET provenance_note = 'edited'",
        "DELETE FROM exposure_profile",
        "UPDATE exposure_weight SET weight_ppm = 1",
        "DELETE FROM exposure_weight",
    ):
        with session_factory() as s, pytest.raises(DatabaseError, match="append-only|immutable"):
            s.execute(text(statement))
    with session_factory() as s, pytest.raises((DatabaseError, IntegrityError)):
        s.execute(  # a float or a numeric string would otherwise be stored as it is
            text(
                "INSERT INTO exposure_weight (profile_id, dimension_key, label_key, weight_ppm) "
                "VALUES (1, 'asset_class', 'property', 5.5)"
            )
        )
    with session_factory() as s, pytest.raises(DatabaseError):
        s.add(
            ExposureWeight(profile_id=1, dimension_key="asset_class", label_key="x", weight_ppm=1)
        )
        s.flush()


def test_two_current_profiles_for_one_date_cannot_exist(
    session_factory: sessionmaker[Session], etf: int
) -> None:
    with session_factory.begin() as s:
        svc.record_profile(s, CTX, etf, _in(_w("cash", "100")))
    with session_factory() as s, pytest.raises(IntegrityError):
        s.add(
            ExposureProfile(
                instrument_id=etf,
                as_at=AT,
                source="manual",
                retrieved_on=TODAY,
                provenance_note="x",
                recorded_at=datetime(2026, 1, 1),
            )
        )
        s.flush()


# --- the fixture -----------------------------------------------------------------------------


def test_the_fixture_loads_profiles_through_the_service_once(
    session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    shutil.copy(
        REPO / "fixtures" / "sample_household" / "household.json", tmp_path / "household.json"
    )
    with session_factory.begin() as s:
        seed_exposure_vocab(s)
        inst_svc.load_fixture_instruments(s, tmp_path)
        assert svc.load_fixture_exposure_profiles(s, tmp_path) == 3
        assert svc.load_fixture_exposure_profiles(s, tmp_path) == 0  # not twice
    with session_factory() as s:
        by_name = {i.name: svc.list_profiles(s, i.id) for i in inst_svc.list_instruments(s)}
        etf = by_name["Sample Broad Market ETF"][0]
        assert sum(w.weight_ppm for w in etf.weights) == 995_000  # the tolerance case
        assert {n: len(p) for n, p in by_name.items()} == {
            "Sample Broad Market ETF": 1,
            "Sample Mining Ltd": 1,
            "Sample Balanced Growth Fund": 1,
        }
    events = _events(session_factory)
    assert len(events) == 3 and {e.source for e in events} == {"fixture"}
    data = json.loads((tmp_path / "household.json").read_text(encoding="utf-8"))
    assert all("exposure_profiles" in i for i in data["instruments"])
