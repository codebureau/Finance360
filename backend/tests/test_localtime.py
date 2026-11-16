"""The Household's local date: the UTC/local boundary (unit + Hypothesis), and its use in the
services (integration, real migrated SQLite)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from finance360.capabilities.household import service as household
from finance360.capabilities.household.service import ValidationError
from finance360.capabilities.position import accounts as accounts_svc
from finance360.capabilities.position import valuations as valuations_svc
from finance360.capabilities.position.accounts import AccountInput, OwnerInput
from finance360.capabilities.position.valuations import ValuationInput
from finance360.config import Settings
from finance360.core.audit import AuditContext
from finance360.core.catalogue import seed_catalogue
from finance360.core.localtime import (
    DEFAULT_TIME_ZONE,
    InvalidTimeZoneError,
    local_today,
    parse_time_zone,
)
from finance360.core.models.audit import AuditEvent
from finance360.main import create_app
from support import owner_client

CTX = AuditContext("owner", "owner", "api")
MELBOURNE = "Australia/Melbourne"

# 2026-10-17 20:00 UTC is already 18 October in Melbourne (AEDT, UTC+11): the lagging-UTC window.
LAGGING = datetime(2026, 10, 17, 20, 0, tzinfo=UTC)

instants = st.datetimes(
    min_value=datetime(2000, 1, 1), max_value=datetime(2060, 1, 1), timezones=st.just(UTC)
)


def test_default_time_zone_is_melbourne() -> None:
    assert DEFAULT_TIME_ZONE == MELBOURNE


def test_melbourne_is_a_day_ahead_of_utc_for_part_of_the_day() -> None:
    assert LAGGING.date() == date(2026, 10, 17)
    assert local_today(MELBOURNE, LAGGING) == date(2026, 10, 18)


def test_the_boundary_is_midnight_local_time_in_summer_and_winter() -> None:
    # AEDT (UTC+11): local midnight is 13:00 UTC the day before.
    assert local_today(MELBOURNE, datetime(2026, 10, 17, 12, 59, tzinfo=UTC)) == date(2026, 10, 17)
    assert local_today(MELBOURNE, datetime(2026, 10, 17, 13, 0, tzinfo=UTC)) == date(2026, 10, 18)
    # AEST (UTC+10): local midnight is 14:00 UTC the day before.
    assert local_today(MELBOURNE, datetime(2026, 7, 1, 13, 59, tzinfo=UTC)) == date(2026, 7, 1)
    assert local_today(MELBOURNE, datetime(2026, 7, 1, 14, 0, tzinfo=UTC)) == date(2026, 7, 2)


def test_a_zone_behind_utc_lags_it() -> None:
    instant = datetime(2026, 10, 17, 5, 0, tzinfo=UTC)
    assert local_today("Pacific/Honolulu", instant) == date(2026, 10, 16)


def test_utc_zone_is_the_utc_date() -> None:
    assert local_today("UTC", LAGGING) == LAGGING.date()


def test_the_default_clock_is_used_when_no_instant_is_given() -> None:
    assert abs(local_today(MELBOURNE) - datetime.now(UTC).date()) <= timedelta(days=1)


def test_unknown_names_and_naive_datetimes_are_refused() -> None:
    for bad in ("", "Mars/Olympus_Mons", "../etc/passwd"):
        with pytest.raises(InvalidTimeZoneError):
            parse_time_zone(bad)
    with pytest.raises(ValueError, match="aware"):
        local_today(MELBOURNE, datetime(2026, 10, 17, 20, 0))  # noqa: DTZ001


@given(instants, st.sampled_from([MELBOURNE, "UTC", "Pacific/Honolulu", "Asia/Kolkata"]))
def test_local_date_is_within_a_day_of_the_utc_date_and_matches_the_zone(
    instant: datetime, zone: str
) -> None:
    local = local_today(zone, instant)
    assert local == instant.astimezone(ZoneInfo(zone)).date()
    assert abs((local - instant.date()).days) <= 1


@given(instants)
def test_melbourne_is_never_behind_utc_and_is_ahead_exactly_when_utc_is_late(
    instant: datetime,
) -> None:
    offset = instant.astimezone(ZoneInfo(MELBOURNE)).utcoffset()
    assert offset is not None
    local = local_today(MELBOURNE, instant)
    assert local >= instant.date()
    minutes_into_utc_day = instant.hour * 60 + instant.minute
    ahead = minutes_into_utc_day + int(offset.total_seconds() // 60) >= 24 * 60
    assert (local > instant.date()) == ahead


# --- the services use the Household's zone, not the server's date -------------------------


@pytest.fixture
def factory(session_factory: sessionmaker[Session]) -> sessionmaker[Session]:
    with session_factory.begin() as s:
        household.ensure_household(s)
        seed_catalogue(s)
    return session_factory


@pytest.fixture
def at_lagging_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    """Freeze the clock at the instant when Melbourne's date is a day ahead of UTC's."""
    monkeypatch.setattr(household, "local_today", lambda zone: local_today(zone, LAGGING))


def test_a_new_household_has_the_default_time_zone(factory: sessionmaker[Session]) -> None:
    with factory() as s:
        assert household.get_household(s).time_zone == MELBOURNE


def test_changing_the_time_zone_is_validated_and_audited(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        household.set_time_zone(s, CTX, " Pacific/Auckland ")
    with factory() as s:
        assert household.get_household(s).time_zone == "Pacific/Auckland"
    for bad in ("", "Nowhere/Land"):
        with factory.begin() as s, pytest.raises(ValidationError) as exc:
            household.set_time_zone(s, CTX, bad)
        assert exc.value.errors[0].field == "time_zone"
    with factory() as s:
        events = list(s.scalars(select(AuditEvent)))
    assert [(e.action, e.before, e.after) for e in events] == [
        ("update", {"time_zone": MELBOURNE}, {"time_zone": "Pacific/Auckland"})
    ]


def test_member_date_of_birth_uses_the_household_local_date(
    factory: sessionmaker[Session], at_lagging_utc: None
) -> None:
    # 17 Oct is still "today" by the UTC date but already yesterday in Melbourne.
    with factory.begin() as s:
        household.add_member(s, CTX, "Alex Sample", date(2026, 10, 17))
    with factory.begin() as s:
        household.set_time_zone(s, CTX, "UTC")
    with factory.begin() as s, pytest.raises(ValidationError) as exc:
        household.add_member(s, CTX, "Jordan Sample", date(2026, 10, 17))
    assert "in the past" in exc.value.errors[0].message


def test_valuation_dates_and_defaults_use_the_household_local_date(
    factory: sessionmaker[Session], at_lagging_utc: None
) -> None:
    with factory.begin() as s:
        member = household.add_member(s, CTX, "Alex Sample", date(1984, 3, 12))
        account = accounts_svc.create_account(
            s,
            CTX,
            AccountInput(
                name="Everyday",
                category_key="cash",
                account_type_key="transaction_account",
                owners=[OwnerInput(member.id)],
            ),
        )
        account_id = account.id
    with factory.begin() as s:  # 18 Oct is today in Melbourne: accepted
        valuations_svc.add_valuation(
            s, CTX, account_id, ValuationInput(date(2026, 10, 18), "10.00", "manual", "Checked")
        )
        defaulted = valuations_svc.add_valuation(
            s, CTX, account_id, ValuationInput(None, "11.00", "statement", "Statement")
        )
        assert defaulted.as_at == date(2026, 10, 18)  # the default as-at is the local date
    with factory.begin() as s:
        household.set_time_zone(s, CTX, "UTC")
    with factory.begin() as s, pytest.raises(ValidationError) as exc:  # future in UTC
        valuations_svc.add_valuation(
            s, CTX, account_id, ValuationInput(date(2026, 10, 18), "1", "estimate", "x")
        )
    assert exc.value.errors[0].field == "as_at"


def test_closing_defaults_to_the_household_local_date_through_the_api(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", port=8000, git_sha="dev", static_dir=None)
    with owner_client(create_app(settings)) as client:
        member = client.post(
            "/api/household/members", json={"name": "Alex Sample", "date_of_birth": "1984-03-12"}
        ).json()["id"]
        account = client.post(
            "/api/accounts",
            json={
                "name": "Everyday",
                "category_key": "cash",
                "account_type_key": "transaction_account",
                "owners": [{"member_id": member}],
            },
        ).json()["id"]
        before = local_today(MELBOURNE)
        closed = client.post(f"/api/accounts/{account}/close", json={}).json()["closed_on"]
        assert closed in {before.isoformat(), local_today(MELBOURNE).isoformat()}
