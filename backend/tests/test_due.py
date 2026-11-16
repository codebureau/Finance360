"""What's due (ADR-0020): the rules, the Effort level filter, Snoozes and the API.

Integration tests against a real temporary SQLite database built by the real migrations, with
``today`` and the Effort level injected (no clock). Every name and figure is invented.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from finance360.capabilities.due import service
from finance360.capabilities.household import service as household
from finance360.capabilities.position import accounts, valuations
from finance360.config import Settings
from finance360.core import due as core_due
from finance360.core.audit import AuditContext
from finance360.core.catalogue import seed_catalogue
from finance360.core.due import IMPORTANT, OPTIONAL, DueItem, DueRule, EffortLevel, Every
from finance360.core.errors import ValidationError
from finance360.core.models.audit import AuditEvent
from finance360.core.models.tax_record import TaxRecord
from finance360.main import create_app, register_due_rules
from support import TestClient, owner_client

CTX = AuditContext("owner", "login:1", "api")
TODAY = date(2026, 11, 3)
LONG_AGO = date(2020, 1, 1)
REPO = Path(__file__).resolve().parents[2]
MANUAL = "position.manual_valuation"
SUPER = "position.super_valuation"
SLOW = "position.slow_valuation"
STATEMENT = "tax.income_statement"


@pytest.fixture(autouse=True)
def rules() -> Iterator[None]:
    """The real rules, registered as the composition root does; the registry is restored after."""
    saved = dict(core_due._RULES)
    core_due._RULES.clear()
    register_due_rules()
    yield
    core_due._RULES.clear()
    core_due._RULES.update(saved)


class Db:
    def __init__(self, factory: sessionmaker[Session]) -> None:
        self.factory = factory
        with factory.begin() as s:
            household.ensure_household(s)
            seed_catalogue(s)
            self.member = household.add_member(s, CTX, "Alex Sample", date(1984, 3, 12)).id

    def account(
        self,
        category: str,
        type_key: str,
        *,
        name: str = "Sample account",
        last: date | None = None,
        opened_on: date | None = None,
    ) -> int:
        with self.factory.begin() as s:
            data = accounts.AccountInput(
                name=name,
                category_key=category,
                account_type_key=type_key,
                owners=[accounts.OwnerInput(self.member)],
                opened_on=opened_on,
            )
            account_id = accounts.create_account(s, CTX, data).id
            if last is not None:
                self.value(account_id, last, s)
            return account_id

    def value(self, account_id: int, as_at: date, s: Session | None = None) -> None:
        def add(session: Session) -> None:
            data = valuations.ValuationInput(as_at, "100.00", "statement", "Invented statement")
            valuations.add_valuation(session, CTX, account_id, data, today=TODAY)

        if s is not None:
            add(s)
        else:
            with self.factory.begin() as session:
                add(session)

    def due(self, level: EffortLevel, today: date = TODAY) -> service.WhatsDue:
        with self.factory() as s:
            return service.whats_due(s, today=today, level=level)

    def keys(self, level: EffortLevel, today: date = TODAY) -> set[str]:
        return {i.rule_key for i in self.due(level, today).items}


@pytest.fixture
def db(session_factory: sessionmaker[Session]) -> Db:
    return Db(session_factory)


# --- cadences, per level -------------------------------------------------------------------

#: (category, account type, rule, days at Light/Steady/Thorough; None = not asked)
CADENCES = [
    ("cash", "savings_account", MANUAL, {"light": 92, "steady": 31, "thorough": 31}),
    ("loans_credit", "home_loan", MANUAL, {"light": 92, "steady": 31, "thorough": 31}),
    ("superannuation", "super_fund_account", SUPER, {"light": 183, "steady": 31, "thorough": 31}),
    ("property", "principal_residence", SLOW, {"light": None, "steady": 366, "thorough": 366}),
    ("vehicles", "vehicle", SLOW, {"light": None, "steady": 366, "thorough": 366}),
]


@pytest.mark.parametrize("level", ["light", "steady", "thorough"])
@pytest.mark.parametrize(("category", "type_key", "rule", "days"), CADENCES)
def test_each_cadence_starts_on_its_day_and_not_a_day_before(
    db: Db, category: str, type_key: str, rule: str, days: dict[str, int | None], level: str
) -> None:
    n = days[level]
    if n is None:  # not asked at this level: even a very old figure gives nothing
        db.account(category, type_key, last=LONG_AGO)
        assert db.keys(level) == set()  # type: ignore[arg-type]
        return
    db.account(category, type_key, last=TODAY - timedelta(days=n - 1))
    assert db.keys(level) == set()  # type: ignore[arg-type]
    other = db.account(category, type_key, name="Older", last=TODAY - timedelta(days=n))
    items = db.due(level).items  # type: ignore[arg-type]
    assert [i.rule_key for i in items] == [rule]
    assert items[0].link == f"/accounts/{other}"
    assert items[0].due_on == TODAY  # last + cadence


def test_light_shows_important_items_only_and_steady_adds_the_optional_ones(db: Db) -> None:
    db.account("cash", "savings_account", name="Cash", last=LONG_AGO)
    db.account("superannuation", "super_fund_account", name="Super", last=LONG_AGO)
    db.account("property", "principal_residence", name="House", last=LONG_AGO)
    assert db.keys("light") == {MANUAL, SUPER}
    assert db.keys("steady") == {MANUAL, SUPER, SLOW}
    assert db.keys("thorough") == {MANUAL, SUPER, SLOW}
    assert {i.importance for i in db.due("light").items} == {IMPORTANT}
    steady = db.due("steady").items
    assert [i.importance for i in steady] == [IMPORTANT, IMPORTANT, OPTIONAL]  # important first


def test_light_drops_an_optional_rule_even_when_its_cadence_asks_at_light(db: Db) -> None:
    """Importance decides what Light shows, apart from the cadence table (ADR-0020 section 3)."""
    item = DueItem("test.optional", "x@1", OPTIONAL, "t", "e", TODAY, "/", ("whole",))
    core_due.register(
        DueRule(
            key="test.optional",
            importance=OPTIONAL,
            cadence={"light": Every(1), "steady": Every(1), "thorough": Every(1)},
            suits=("whole",),
            guide_item="An optional check",
            interested=lambda _s: True,
            check=lambda _s, _t, _l: (item,),
        )
    )
    assert "test.optional" not in db.keys("light")
    assert "test.optional" in db.keys("steady")
    assert "test.optional" in db.keys("thorough")


def test_the_level_defaults_to_the_households_and_is_light_until_chosen(db: Db) -> None:
    db.account("property", "principal_residence", last=LONG_AGO)
    with db.factory.begin() as s:
        assert service.current_level(s) == "light"
        assert service.whats_due(s, today=TODAY).items == ()  # the optional item is not asked
        household.set_effort_level(s, CTX, "steady")
    with db.factory() as s:
        assert service.current_level(s) == "steady"
        assert [i.rule_key for i in service.whats_due(s, today=TODAY).items] == [SLOW]


def test_a_closed_account_never_prompts(db: Db) -> None:
    account_id = db.account("cash", "savings_account", last=LONG_AGO)
    assert db.keys("steady") == {MANUAL}
    with db.factory.begin() as s:
        accounts.close_account(s, CTX, account_id, date(2026, 1, 1), confirm_non_zero=True)
    assert db.keys("steady") == set()


def test_an_account_with_no_valuation_is_due_from_its_opened_date(db: Db) -> None:
    db.account("cash", "savings_account", name="New", opened_on=date(2026, 10, 20))
    item = db.due("light").items[0]
    assert item.rule_key == MANUAL
    assert item.due_on == date(2026, 10, 20)
    assert item.subject_key.endswith("@none")
    assert db.keys("light", today=date(2026, 10, 19)) == set()  # not open yet


def test_a_derived_account_is_never_asked_for_a_valuation(db: Db) -> None:
    from finance360.capabilities.position import derived

    derived.register_hook("simple_balance", lambda *_a: None)  # type: ignore[arg-type,return-value]
    try:
        db.account("cash", "savings_account", last=LONG_AGO)
        assert db.keys("steady") == set()
    finally:
        derived._HOOKS.pop("simple_balance", None)


def test_no_data_of_a_kind_gives_no_item(db: Db) -> None:
    assert db.due("thorough").items == ()  # no Accounts, no Tax records: no prompts at all


def test_interest_absent_gives_nothing_even_if_the_check_would_find_something(db: Db) -> None:
    item = DueItem("test.rule", "x@1", IMPORTANT, "t", "e", TODAY, "/", ("whole",))
    core_due.register(
        DueRule(
            key="test.rule",
            importance=IMPORTANT,
            cadence={"light": Every(1), "steady": Every(1), "thorough": Every(1)},
            suits=("whole",),
            guide_item="Test",
            interested=lambda _s: False,
            check=lambda _s, _t, _l: (item,),
        )
    )
    assert "test.rule" not in db.keys("thorough")


# --- the income statement ------------------------------------------------------------------


def statement(db: Db, member: int, start: date, end: date) -> None:
    with db.factory.begin() as s:
        s.add(
            TaxRecord(
                member_id=member,
                record_type="income_statement",
                from_on=start,
                to_on=end,
                description="Income statement, Sample Employer",
                payload_json="{}",
                created_at=datetime(2026, 1, 1),
            )
        )


def test_the_income_statement_starts_on_15_july_not_14_july(db: Db) -> None:
    statement(db, db.member, date(2024, 7, 1), date(2025, 6, 30))  # FY 2024-25 only
    assert db.keys("steady", date(2026, 7, 14)) == set()
    on_the_15th = db.due("light", date(2026, 7, 15)).items
    assert [(i.rule_key, i.subject_key) for i in on_the_15th] == [
        (STATEMENT, f"member:{db.member}@fy2026")
    ]
    assert on_the_15th[0].due_on == date(2026, 7, 15)
    assert "2025-26" in on_the_15th[0].title
    assert "Alex Sample" in on_the_15th[0].title
    for level in ("light", "steady", "thorough"):  # one date at every level
        assert db.keys(level, date(2026, 7, 15)) == {STATEMENT}  # type: ignore[arg-type]
        assert db.keys(level, date(2026, 7, 14)) == set()  # type: ignore[arg-type]


def test_only_the_latest_ended_year_prompts_and_a_statement_for_it_silences_it(db: Db) -> None:
    statement(db, db.member, date(2023, 7, 1), date(2024, 6, 30))  # FY 2023-24 on record
    items = db.due("light", date(2026, 8, 1)).items
    assert [i.subject_key for i in items] == [f"member:{db.member}@fy2026"]  # not FY25, not FY24
    statement(db, db.member, date(2025, 7, 1), date(2026, 6, 30))  # the latest ended year
    assert db.keys("light", date(2026, 8, 1)) == set()
    # A year later the newest ended year is FY 2026-27, which has no statement yet
    again = db.due("light", date(2027, 7, 20)).items
    assert [i.subject_key for i in again] == [f"member:{db.member}@fy2027"]


def test_a_member_with_no_statement_on_record_is_not_asked(db: Db) -> None:
    assert db.keys("light", date(2026, 8, 1)) == set()


# --- Snoozes -------------------------------------------------------------------------------


def snoozed_account(db: Db) -> DueItem:
    db.account("cash", "savings_account", last=date(2026, 6, 1))
    return db.due("steady").items[0]


def test_a_snooze_hides_the_item_and_lists_it_as_snoozed(db: Db) -> None:
    item = snoozed_account(db)
    with db.factory.begin() as s:
        row = service.snooze(s, CTX, item.rule_key, item.subject_key, "week", today=TODAY)
        assert row.until_on == TODAY + timedelta(days=7)
    result = db.due("steady")
    assert result.items == ()
    assert [(s.item, s.until_on) for s in result.snoozed] == [(item, TODAY + timedelta(days=7))]


def test_a_snooze_expires_by_comparison_with_today_and_nothing_is_cleaned_up(db: Db) -> None:
    item = snoozed_account(db)
    with db.factory.begin() as s:
        service.snooze(s, CTX, item.rule_key, item.subject_key, "week", today=TODAY)
    last_day = TODAY + timedelta(days=6)
    assert db.due("steady", last_day).items == ()  # still snoozed the day before
    after = db.due("steady", TODAY + timedelta(days=7))  # until_on itself: back
    assert [i.subject_key for i in after.items] == [item.subject_key]
    assert after.snoozed == ()
    with db.factory() as s:  # the inert row is still there: expiry is computed
        assert len(service.get_snooze(s, 1).subject_key) > 0


def test_a_month_snooze_lasts_longer_than_a_week(db: Db) -> None:
    item = snoozed_account(db)
    with db.factory.begin() as s:
        service.snooze(s, CTX, item.rule_key, item.subject_key, "month", today=TODAY)
    assert db.due("steady", TODAY + timedelta(days=8)).items == ()
    assert db.due("steady", TODAY + timedelta(days=31)).items != ()


def test_skip_this_one_hides_this_period_but_not_the_next(db: Db) -> None:
    account_id = db.account("cash", "savings_account", last=date(2026, 6, 1))
    this_period = db.due("steady").items[0]
    with db.factory.begin() as s:
        row = service.snooze(s, CTX, this_period.rule_key, this_period.subject_key, "skip")
        assert row.until_on is None
    assert db.due("steady").items == ()
    assert db.due("steady", TODAY + timedelta(days=400)).items == ()  # no date brings it back
    # A newer figure that later goes stale is a new period with its own subject: it shows
    db.value(account_id, date(2026, 8, 1))
    later = db.due("steady", date(2026, 9, 5)).items
    assert [i.subject_key for i in later] == [f"account:{account_id}@2026-08-01"]
    assert later[0].subject_key != this_period.subject_key


def test_snoozing_again_updates_the_row_and_unsnooze_deletes_it_both_audited(db: Db) -> None:
    item = snoozed_account(db)
    with db.factory.begin() as s:
        first = service.snooze(s, CTX, item.rule_key, item.subject_key, "week", today=TODAY)
        first_id = first.id
    with db.factory.begin() as s:
        again = service.snooze(s, CTX, item.rule_key, item.subject_key, "skip")
        assert again.id == first_id and again.until_on is None
    with db.factory.begin() as s:
        service.unsnooze(s, CTX, first_id)
    assert len(db.due("steady").items) == 1
    with db.factory() as s:
        events = [
            (e.entity_type, e.action)
            for e in s.scalars(select(AuditEvent).where(AuditEvent.entity_type == "due_snooze"))
        ]
    assert events == [("due_snooze", "create"), ("due_snooze", "update"), ("due_snooze", "delete")]


def test_a_snooze_is_checked(db: Db) -> None:
    with db.factory() as s:
        with pytest.raises(ValidationError) as unknown:
            service.snooze(s, CTX, "no.such.rule", "x@1", "week")
        assert [e.field for e in unknown.value.errors] == ["rule_key"]
        with pytest.raises(ValidationError) as bad:
            service.snooze(s, CTX, MANUAL, "x@1", "forever")
        assert [e.field for e in bad.value.errors] == ["snooze"]


# --- failure and wording -------------------------------------------------------------------


def test_a_rule_that_raises_is_reported_and_does_not_blank_the_list(db: Db) -> None:
    db.account("cash", "savings_account", last=LONG_AGO)

    def explode(_s: Session, _t: date, _l: EffortLevel) -> tuple[DueItem, ...]:
        raise RuntimeError("a bug in one rule")

    core_due.register(
        DueRule(
            key="test.broken",
            importance=IMPORTANT,
            cadence={"light": Every(1), "steady": Every(1), "thorough": Every(1)},
            suits=("whole",),
            guide_item="A broken check",
            interested=lambda _s: True,
            check=explode,
        )
    )
    result = db.due("light")
    assert {i.rule_key for i in result.items} == {MANUAL}  # the other rules still answer
    assert [(f.rule_key, f.name) for f in result.failed_rules] == [
        ("test.broken", "A broken check")
    ]


def test_no_title_or_effect_says_overdue(db: Db) -> None:
    db.account("cash", "savings_account", name="Cash", last=LONG_AGO)
    db.account("cash", "savings_account", name="Never valued")
    db.account("superannuation", "super_fund_account", last=LONG_AGO)
    db.account("property", "principal_residence", last=LONG_AGO)
    statement(db, db.member, date(2024, 7, 1), date(2025, 6, 30))
    items = db.due("thorough", date(2026, 8, 1)).items
    assert {i.rule_key for i in items} == {MANUAL, SUPER, SLOW, STATEMENT}
    for item in items:
        words = f"{item.title} {item.effect}".lower()
        assert not {"overdue", "late", "missed", "behind", "failed"} & set(words.split()), words


# --- the Effort level and the API ----------------------------------------------------------


@pytest.fixture
def sample(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(service, "due_today", lambda _s: TODAY)  # the API has no clock to inject
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(REPO / "fixtures" / "sample_household" / "household.json", data / "household.json")
    settings = Settings(data_dir=data, port=8000, git_sha="dev", static_dir=None)
    with owner_client(create_app(settings)) as client:
        yield client


def test_the_sample_household_is_steady_and_the_valuation_rules_have_something_to_show(
    sample: TestClient,
) -> None:
    body = sample.get("/api/due").json()
    assert body["level"] == "steady"
    # The dormant Accounts are old whatever the date; the other Accounts are old enough by now.
    # ...and Jordan's FY 2026-27 car record waits for a Rule figure nobody has captured.
    assert {i["rule_key"] for i in body["items"]} == {
        MANUAL,
        SUPER,
        SLOW,
        "rule_figures.not_captured",
    }
    titles = " ".join(i["title"] for i in body["items"])
    assert "Dormant savings (Sample Bank)" in titles
    assert "Dormant super fund (Sample Super)" in titles
    assert "Old trailer (Sample)" in titles
    assert body["failed_rules"] == []
    assert body["snoozed"] == []


def test_deleting_the_latest_income_statement_makes_whats_due_ask_for_it(
    sample: TestClient,
) -> None:
    """Jordan has statements for FY 2023-24 and 2025-26. Without the newer one, the latest ended
    financial year has none, so the rule asks (the 'How to test' step for the tax rule)."""
    jordan = next(
        m["id"]
        for m in sample.get("/api/household").json()["members"]
        if m["name"].startswith("Jordan")
    )
    statements = [
        r
        for r in sample.get("/api/tax/records", params={"member_id": jordan}).json()
        if r["record_type"] == "income_statement"
    ]
    newest = max(statements, key=lambda r: r["from_on"])
    assert newest["from_on"] == "2025-07-01"
    assert STATEMENT not in {i["rule_key"] for i in sample.get("/api/due").json()["items"]}
    assert sample.delete(f"/api/tax/records/{newest['id']}").status_code == 204
    item = next(i for i in sample.get("/api/due").json()["items"] if i["rule_key"] == STATEMENT)
    assert "Jordan Sample" in item["title"]
    assert item["subject_key"] == f"member:{jordan}@fy2026"


def test_changing_the_effort_level_is_an_audited_household_update(sample: TestClient) -> None:
    before = sample.get("/api/due").json()
    assert (
        sample.put(
            "/api/household", json={"name": "The Sample Household", "effort_level": "light"}
        ).status_code
        == 200
    )
    assert sample.get("/api/household").json()["effort_level"] == "light"
    light = sample.get("/api/due").json()
    assert light["level"] == "light"
    assert SLOW in {i["rule_key"] for i in before["items"]}
    assert SLOW not in {i["rule_key"] for i in light["items"]}
    page = sample.get("/api/audit", params={"entity_type": "household"}).json()
    changes = [e for e in page["items"] if "Effort level" in e["summary"]]
    assert len(changes) == 1
    assert changes[0]["action"] == "update"
    assert changes[0]["source"] == "api"
    assert changes[0]["changes"] == [
        {"field": "Effort level", "before": "steady", "after": "light"}
    ]


def test_a_bad_effort_level_is_refused_in_the_shared_error_shape(sample: TestClient) -> None:
    res = sample.put("/api/household", json={"name": "X", "effort_level": "relentless"})
    assert res.status_code == 422
    assert res.json() == {
        "errors": [{"field": "effort_level", "message": "Choose Light, Steady or Thorough."}]
    }
    assert sample.get("/api/household").json()["effort_level"] == "steady"


def test_snooze_and_unsnooze_through_the_api(sample: TestClient) -> None:
    item = sample.get("/api/due").json()["items"][0]
    put = sample.put(
        "/api/due/snoozes",
        json={"rule_key": item["rule_key"], "subject_key": item["subject_key"], "snooze": "month"},
    )
    assert put.status_code == 200, put.text
    snooze_id = put.json()["id"]
    body = sample.get("/api/due").json()
    assert item["subject_key"] not in {i["subject_key"] for i in body["items"]}
    assert [s["snooze_id"] for s in body["snoozed"]] == [snooze_id]
    assert body["snoozed"][0]["item"]["subject_key"] == item["subject_key"]
    assert sample.delete(f"/api/due/snoozes/{snooze_id}").status_code == 204
    assert item["subject_key"] in {i["subject_key"] for i in sample.get("/api/due").json()["items"]}
    assert sample.delete(f"/api/due/snoozes/{snooze_id}").status_code == 404


def test_a_bad_snooze_is_refused_in_the_shared_error_shape(sample: TestClient) -> None:
    res = sample.put(
        "/api/due/snoozes", json={"rule_key": "no.such", "subject_key": "x@1", "snooze": "week"}
    )
    assert res.status_code == 422
    assert res.json()["errors"][0]["field"] == "rule_key"
