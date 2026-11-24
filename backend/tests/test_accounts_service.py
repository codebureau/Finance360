"""Account, ownership and Tag services: pure rules (unit, Hypothesis) and behaviour with audit
(integration, real migrated SQLite)."""

from __future__ import annotations

import json
import tempfile
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from finance360.capabilities.household import service as household
from finance360.capabilities.household.service import (
    MemberInUseError,
    NotFoundError,
    ValidationError,
)
from finance360.capabilities.position import accounts as svc
from finance360.capabilities.position.accounts import AccountInput, OwnerInput
from finance360.core.audit import AuditContext
from finance360.core.catalogue import seed_catalogue
from finance360.core.db import db_path, make_engine, make_session_factory
from finance360.core.migrate import migrate
from finance360.core.models.account import Account, AccountOwner, Tag
from finance360.core.models.audit import AuditEvent

CTX = AuditContext("owner", "owner", "api")
TODAY = date(2026, 10, 17)


def _fields(exc: pytest.ExceptionInfo[ValidationError]) -> dict[str, str]:
    return {e.field: e.message for e in exc.value.errors}


def _simple(member_id: int, **kw: object) -> AccountInput:
    base = {
        "name": "Everyday",
        "category_key": "cash",
        "account_type_key": "transaction_account",
        "owners": [OwnerInput(member_id)],
    }
    return AccountInput(**{**base, **kw})


def _joint(
    m1: int, m2: int, s1: int | None = None, s2: int | None = None, **kw: object
) -> AccountInput:
    owners = [OwnerInput(m1, s1), OwnerInput(m2, s2)]
    return _simple(m1, ownership="joint", owners=owners, **kw)


def _events(factory: sessionmaker[Session]) -> list[AuditEvent]:
    with factory() as s:
        return list(s.scalars(select(AuditEvent).where(AuditEvent.entity_type == "account")))


@pytest.fixture
def factory(session_factory: sessionmaker[Session]) -> sessionmaker[Session]:
    with session_factory.begin() as s:
        household.ensure_household(s)
        seed_catalogue(s)
        household.add_member(s, CTX, "Alex Sample", date(1984, 3, 12), today=TODAY)
        household.add_member(s, CTX, "Jordan Sample", date(1986, 9, 30), today=TODAY)
    return session_factory


@pytest.fixture
def ids(factory: sessionmaker[Session]) -> tuple[int, int]:
    with factory() as s:
        a, b = household.list_members(s)
        return a.id, b.id


@pytest.fixture(autouse=True)
def _no_leaked_guards() -> Iterator[None]:
    before = list(household._member_delete_guards)  # noqa: SLF001
    yield
    household._member_delete_guards[:] = before  # noqa: SLF001


# --- pure rules ----------------------------------------------------------------------------


def test_percent_formatting_uses_integers() -> None:
    assert [svc.format_percent(bp) for bp in (10000, 6000, 3333, 5050, 1, 100)] == [
        "100%", "60%", "33.33%", "50.5%", "0.01%", "1%",
    ]  # fmt: skip


def test_even_shares_are_5000_each_for_two_and_sums_for_any_count() -> None:
    assert svc.even_shares([7, 9]) == {7: 5000, 9: 5000}
    assert svc.even_shares([1, 2, 3]) == {1: 3334, 2: 3333, 3: 3333}


@given(st.sets(st.integers(1, 10_000), min_size=1, max_size=12))
def test_even_shares_always_sums_to_10000(members: set[int]) -> None:
    shares = svc.even_shares(sorted(members))
    assert sum(shares.values()) == 10000
    assert max(shares.values()) - min(shares.values()) <= 1


def test_resolving_shares_applies_the_sole_and_joint_rules() -> None:
    O = OwnerInput  # noqa: N806, E741
    assert svc.resolve_shares("sole", [O(1)]) == ({1: 10000}, [])
    assert svc.resolve_shares("joint", [O(1), O(2)]) == ({1: 5000, 2: 5000}, [])
    assert svc.resolve_shares("joint", [O(1, 6000), O(2, 4000)]) == ({1: 6000, 2: 4000}, [])
    for ownership, owners, message in [
        ("sole", [], "exactly one Member"),
        ("sole", [O(1), O(2)], "exactly one Member"),
        ("sole", [O(1, 5000)], "sole Member holds 100%"),
        ("joint", [O(1)], "at least two Members"),
        ("joint", [O(1), O(1)], "only once"),
        ("joint", [O(1, 6000), O(2)], "every Member"),
        ("joint", [O(1, 6000), O(2, 5000)], "add up to 100% (they add up to 110%)"),
        ("joint", [O(1, 10000), O(2, 0)], "above 0%"),
        ("joint", [O(1, 10001), O(2, -1)], "above 0%"),
        ("partner", [O(1)], "sole or joint"),
    ]:
        shares, errors = svc.resolve_shares(ownership, owners)
        assert shares == {}
        assert message in errors[0].message


@given(
    st.sampled_from(["sole", "joint", "other"]),
    st.lists(
        st.builds(
            OwnerInput,
            st.integers(1, 6),
            st.none() | st.integers(-100, 12_000),
        ),
        max_size=6,
    ),
)
def test_resolved_shares_are_always_valid_or_rejected(
    ownership: str, owners: list[OwnerInput]
) -> None:
    shares, errors = svc.resolve_shares(ownership, owners)
    if errors:
        assert shares == {}
        return
    assert sum(shares.values()) == 10000
    assert all(0 < s <= 10000 for s in shares.values())
    assert set(shares) == {o.member_id for o in owners}
    assert (len(shares) == 1) == (ownership == "sole")


def test_tags_are_trimmed_deduplicated_ignoring_case_and_checked() -> None:
    tags, errors = svc.normalise_tags(["  Retire  ment ", "retire ment", "", "Growth"])
    assert (tags, errors) == (["Retire ment", "Growth"], [])
    assert svc.normalise_tags(["x" * 51])[1][0].field == "tags"
    assert svc.normalise_tags([f"t{i}" for i in range(21)])[1][0].field == "tags"


# --- create / read -------------------------------------------------------------------------


def test_create_stores_the_account_with_defaults_and_audits_it(
    factory: sessionmaker[Session], ids: tuple[int, int]
) -> None:
    with factory.begin() as s:
        a = svc.create_account(s, CTX, _simple(ids[0], notes="  hello ", tags=["Everyday"]))
        account_id = a.id
    with factory() as s:
        a = svc.get_account(s, account_id)
        assert (a.name, a.currency, a.ownership, a.notes) == ("Everyday", "AUD", "sole", "hello")
        assert (a.category.key, a.account_type.key) == ("cash", "transaction_account")
        assert [(o.member_id, o.share_bp) for o in a.owners] == [(ids[0], 10000)]
        assert [t.name for t in a.tags] == ["Everyday"]
        assert a.opened_on is None and a.closed_on is None
    (event,) = _events(factory)
    assert (event.action, event.entity_id, event.actor_kind, event.source) == (
        "create", account_id, "owner", "api",
    )  # fmt: skip
    assert event.before is None and event.after is not None
    assert event.after["owners"] == [{"member_id": ids[0], "share_bp": 10000}]
    assert event.after["tags"] == ["Everyday"] and event.after["currency"] == "AUD"
    assert "Alex Sample" in event.summary


def test_a_joint_account_defaults_to_even_shares(
    factory: sessionmaker[Session], ids: tuple[int, int]
) -> None:
    with factory.begin() as s:
        a = svc.create_account(s, CTX, _joint(*ids))
        assert sorted(o.share_bp for o in a.owners) == [5000, 5000]
        assert a.ownership == "joint"


def test_a_currency_override_is_stored_as_an_iso_code(
    factory: sessionmaker[Session], ids: tuple[int, int]
) -> None:
    with factory.begin() as s:
        assert svc.create_account(s, CTX, _simple(ids[0], currency=" gbp ")).currency == "GBP"


def test_invalid_input_is_rejected_with_a_message_per_field_and_nothing_written(
    factory: sessionmaker[Session], ids: tuple[int, int]
) -> None:
    bad = _simple(
        ids[0],
        name="  ",
        category_key="nope",
        account_type_key="nope",
        currency="ZZZ",
        notes="n" * 2001,
        owners=[OwnerInput(999)],
    )
    with pytest.raises(ValidationError) as caught, factory.begin() as s:
        svc.create_account(s, CTX, bad)
    assert set(_fields(caught)) == {
        "name", "category", "account_type", "currency", "notes", "owners",
    }  # fmt: skip
    with factory() as s:
        assert s.scalar(select(func.count()).select_from(Account)) == 0
    assert _events(factory) == []


def test_the_account_type_must_belong_to_the_category(
    factory: sessionmaker[Session], ids: tuple[int, int]
) -> None:
    with pytest.raises(ValidationError) as caught, factory.begin() as s:
        svc.create_account(s, CTX, _simple(ids[0], account_type_key="home_loan"))
    assert "does not belong to Cash" in _fields(caught)["account_type"]


def test_shares_that_do_not_sum_to_100_percent_are_rejected(
    factory: sessionmaker[Session], ids: tuple[int, int]
) -> None:
    with pytest.raises(ValidationError) as caught, factory.begin() as s:
        svc.create_account(s, CTX, _joint(ids[0], ids[1], 6000, 5000))
    assert "add up to 100%" in _fields(caught)["owners"]


def test_get_unknown_account_is_not_found(factory: sessionmaker[Session]) -> None:
    with factory() as s, pytest.raises(NotFoundError):
        svc.get_account(s, 999)


def test_the_database_itself_refuses_a_zero_or_oversized_share(
    factory: sessionmaker[Session], ids: tuple[int, int]
) -> None:
    with factory.begin() as s:
        account_id = svc.create_account(s, CTX, _simple(ids[0])).id
    for bad in (0, 10001):
        with pytest.raises(IntegrityError), factory.begin() as s:
            s.add(AccountOwner(account_id=account_id, member_id=ids[1], share_bp=bad))


# --- edit ----------------------------------------------------------------------------------


def test_edit_changes_ownership_and_tags_and_audits_before_and_after(
    factory: sessionmaker[Session], ids: tuple[int, int]
) -> None:
    with factory.begin() as s:
        account_id = svc.create_account(s, CTX, _joint(*ids, tags=["a", "b"])).id
    with factory.begin() as s:
        svc.update_account(s, CTX, account_id, _joint(*ids, 6000, 4000))
    with factory() as s:
        a = svc.get_account(s, account_id)
        assert {o.member_id: o.share_bp for o in a.owners} == {ids[0]: 6000, ids[1]: 4000}
        assert a.tags == []
        assert s.scalar(select(func.count()).select_from(Tag)) == 0  # unused Tags are pruned
    create, update = _events(factory)
    assert update.action == "update" and update.before == create.after
    assert update.after is not None and update.after["tags"] == []
    assert [o["share_bp"] for o in update.after["owners"]] == [6000, 4000]


def test_edit_switching_joint_to_sole_removes_the_other_owner(
    factory: sessionmaker[Session], ids: tuple[int, int]
) -> None:
    with factory.begin() as s:
        account_id = svc.create_account(s, CTX, _joint(*ids)).id
    with factory.begin() as s:
        svc.update_account(s, CTX, account_id, _simple(ids[1]))
    with factory() as s:
        a = svc.get_account(s, account_id)
        assert a.ownership == "sole"
        assert [(o.member_id, o.share_bp) for o in a.owners] == [(ids[1], 10000)]


def test_a_rejected_edit_changes_nothing(
    factory: sessionmaker[Session], ids: tuple[int, int]
) -> None:
    with factory.begin() as s:
        account_id = svc.create_account(s, CTX, _joint(*ids, 6000, 4000)).id
    with pytest.raises(ValidationError), factory.begin() as s:
        svc.update_account(s, CTX, account_id, _joint(*ids, 6000, 5000))
    with factory() as s:
        a = svc.get_account(s, account_id)
        assert sorted(o.share_bp for o in a.owners) == [4000, 6000]
    assert [e.action for e in _events(factory)] == ["create"]


def test_an_unchanged_edit_writes_no_audit_record(
    factory: sessionmaker[Session], ids: tuple[int, int]
) -> None:
    with factory.begin() as s:
        account_id = svc.create_account(s, CTX, _simple(ids[0], tags=["x"])).id
    with factory.begin() as s:
        svc.update_account(s, CTX, account_id, _simple(ids[0], tags=["X"]))
    assert [e.action for e in _events(factory)] == ["create"]


def test_tags_are_reused_across_accounts_case_insensitively(
    factory: sessionmaker[Session], ids: tuple[int, int]
) -> None:
    with factory.begin() as s:
        svc.create_account(s, CTX, _simple(ids[0], name="One", tags=["Retirement", "Growth"]))
        svc.create_account(s, CTX, _simple(ids[1], name="Two", tags=["retirement"]))
    with factory() as s:
        assert svc.list_tags(s) == ["Growth", "Retirement"]
        assert sorted(a.tags[0].name for a in svc.list_accounts(s)) == ["Growth", "Retirement"]
        assert [t.name for a in svc.list_accounts(s) for t in a.tags].count("Retirement") == 2


# --- close ---------------------------------------------------------------------------------


def test_close_sets_closed_on_hides_the_account_and_audits_it(
    factory: sessionmaker[Session], ids: tuple[int, int]
) -> None:
    with factory.begin() as s:
        keep = svc.create_account(s, CTX, _simple(ids[0], name="Keep")).id
        gone = svc.create_account(s, CTX, _simple(ids[0], name="Gone")).id
    with factory.begin() as s:
        svc.close_account(s, CTX, gone, date(2026, 6, 30))
    with factory() as s:
        assert [a.id for a in svc.list_accounts(s)] == [keep]
        everything = svc.list_accounts(s, include_closed=True)
        assert sorted(a.id for a in everything) == sorted([keep, gone])
        assert svc.get_account(s, gone).closed_on == date(2026, 6, 30)
    close = _events(factory)[-1]
    assert (close.action, close.entity_id) == ("close", gone)
    assert close.before is not None and close.before["closed_on"] is None
    assert close.after is not None and close.after["closed_on"] == "2026-06-30"


def test_closing_twice_or_before_opening_is_rejected(
    factory: sessionmaker[Session], ids: tuple[int, int]
) -> None:
    with factory.begin() as s:
        account_id = svc.create_account(s, CTX, _simple(ids[0], opened_on=date(2020, 1, 1))).id
    with pytest.raises(ValidationError) as early, factory.begin() as s:
        svc.close_account(s, CTX, account_id, date(2020, 1, 1))
    assert "after it opened" in _fields(early)["closed_on"]
    with factory.begin() as s:
        svc.close_account(s, CTX, account_id, date(2021, 1, 1))
    with pytest.raises(ValidationError) as again, factory.begin() as s:
        svc.close_account(s, CTX, account_id, date(2022, 1, 1))
    assert "already closed" in _fields(again)["closed_on"]


def test_a_closed_account_can_still_be_edited_but_not_opened_after_it_closed(
    factory: sessionmaker[Session], ids: tuple[int, int]
) -> None:
    with factory.begin() as s:
        account_id = svc.create_account(s, CTX, _simple(ids[0])).id
        svc.close_account(s, CTX, account_id, date(2021, 1, 1))
    with factory.begin() as s:
        svc.update_account(s, CTX, account_id, _simple(ids[0], notes="kept for history"))
    with pytest.raises(ValidationError) as caught, factory.begin() as s:
        svc.update_account(s, CTX, account_id, _simple(ids[0], opened_on=date(2021, 1, 1)))
    assert "open before it closes" in _fields(caught)["opened_on"]


def test_close_runs_the_closing_valuation_step_in_the_same_transaction(
    factory: sessionmaker[Session], ids: tuple[int, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The seam the Valuation service fills: whatever it raises (e.g. needs confirmation) rolls the
    close back."""
    with factory.begin() as s:
        account_id = svc.create_account(s, CTX, _simple(ids[0])).id
    calls: list[tuple[date, bool]] = []

    def step(_s: Session, _c: AuditContext, _a: Account, closed_on: date, confirm: bool) -> None:
        calls.append((closed_on, confirm))
        if not confirm:
            raise svc.CloseNeedsConfirmationError("The latest Valuation is not zero.")

    monkeypatch.setattr(svc, "_write_closing_valuation", step)
    with pytest.raises(svc.CloseNeedsConfirmationError), factory.begin() as s:
        svc.close_account(s, CTX, account_id, date(2026, 6, 30))
    with factory() as s:
        assert svc.get_account(s, account_id).closed_on is None
    with factory.begin() as s:
        svc.close_account(s, CTX, account_id, date(2026, 6, 30), confirm_non_zero=True)
    assert calls == [(date(2026, 6, 30), False), (date(2026, 6, 30), True)]
    assert [e.action for e in _events(factory)] == ["create", "close"]


# --- Member delete guard -------------------------------------------------------------------


def test_position_blocks_deleting_a_member_who_owns_an_account(
    factory: sessionmaker[Session], ids: tuple[int, int]
) -> None:
    svc.register()
    svc.register()  # idempotent
    with factory.begin() as s:
        account_id = svc.create_account(s, CTX, _joint(*ids)).id
    with pytest.raises(MemberInUseError, match="Ownership of an Account"), factory.begin() as s:
        household.delete_member(s, CTX, ids[1])
    with factory.begin() as s:  # closing does not release the Member: history keeps the link
        svc.close_account(s, CTX, account_id, date(2026, 6, 30))
    with pytest.raises(MemberInUseError), factory.begin() as s:
        household.delete_member(s, CTX, ids[1])
    with factory() as s:
        assert len(household.list_members(s)) == 2


def test_a_member_with_no_accounts_can_still_be_deleted(
    factory: sessionmaker[Session], ids: tuple[int, int]
) -> None:
    svc.register()
    with factory.begin() as s:
        svc.create_account(s, CTX, _simple(ids[0]))
    with factory.begin() as s:
        household.delete_member(s, CTX, ids[1])


# --- property: shares sum to 10000 after any valid edit ------------------------------------


@settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(
    st.lists(
        st.tuples(
            st.sampled_from(["sole", "joint"]),
            st.lists(
                st.tuples(st.integers(0, 2), st.none() | st.integers(-5, 10_001)),
                max_size=4,
            ),
        ),
        min_size=1,
        max_size=6,
    )
)
def test_shares_always_sum_to_10000_after_any_edit_and_rejected_edits_change_nothing(
    edits: list[tuple[str, list[tuple[int, int | None]]]],
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        migrate(Path(tmp))
        engine = make_engine(db_path(Path(tmp)))
        factory = make_session_factory(engine)
        try:
            with factory.begin() as s:
                household.ensure_household(s)
                seed_catalogue(s)
                members = [
                    household.add_member(s, CTX, f"M{i}", date(1980, 1, 1), today=TODAY).id
                    for i in range(3)
                ]
                account_id = svc.create_account(s, CTX, _simple(members[0])).id
            for ownership, raw in edits:
                owners = [OwnerInput(members[i], share) for i, share in raw]
                data = _simple(members[0], ownership=ownership, owners=owners)
                try:
                    with factory.begin() as s:
                        svc.update_account(s, CTX, account_id, data)
                except ValidationError:
                    pass
                with factory() as s:
                    a = svc.get_account(s, account_id)
                    shares = [o.share_bp for o in a.owners]
                    assert sum(shares) == 10000
                    assert all(share > 0 for share in shares)
                    assert (len(shares) == 1) == (a.ownership == "sole")
        finally:
            engine.dispose()


# --- first run / fixture -------------------------------------------------------------------


def _write_fixture(path: Path, accounts: list[dict[str, object]]) -> None:
    (path / "household.json").write_text(
        json.dumps(
            {
                "members": [
                    {"name": "Alex Sample", "date_of_birth": "1984-03-12"},
                    {"name": "Jordan Sample", "date_of_birth": "1986-09-30"},
                ],
                "accounts": accounts,
            }
        )
    )


def test_fixture_accounts_load_through_the_service_once(
    session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    _write_fixture(
        tmp_path,
        [
            {
                "name": "Pot",
                "category": "cash",
                "account_type": "savings_account",
                "owners": ["Alex Sample", "Jordan Sample"],
                "currency": "NZD",
                "opened_on": "2020-01-02",
                "tags": ["t"],
            },
            {
                "name": "Solo",
                "category": "vehicles",
                "account_type": "vehicle",
                "owners": ["Jordan Sample"],
            },
        ],
    )
    for _ in range(2):  # a second start adds nothing
        with session_factory.begin() as s:
            seed_catalogue(s)
            household.bootstrap(s, tmp_path)
            svc.load_fixture_accounts(s, tmp_path)
    with session_factory() as s:
        pot, solo = sorted(svc.list_accounts(s), key=lambda a: a.name)
        assert (pot.ownership, pot.currency, pot.opened_on) == ("joint", "NZD", date(2020, 1, 2))
        assert sorted(o.share_bp for o in pot.owners) == [5000, 5000]
        assert solo.ownership == "sole"
    events = _events(session_factory)
    assert [(e.action, e.actor_kind, e.source) for e in events] == [
        ("create", "system", "fixture")
    ] * 2


def test_without_a_fixture_no_accounts_are_loaded(
    session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    with session_factory.begin() as s:
        assert svc.load_fixture_accounts(s, tmp_path) == 0


def test_the_shipped_fixture_has_the_required_shape() -> None:
    fixture = Path(__file__).resolve().parents[2] / "fixtures/sample_household/household.json"
    accounts = json.loads(fixture.read_text(encoding="utf-8"))["accounts"]
    assert len(accounts) == 13
    assert sum(len(a["owners"]) > 1 for a in accounts) == 2  # savings, joint investments
    assert [a["currency"] for a in accounts if a.get("currency", "AUD") != "AUD"] == ["GBP"]
    assert {a["category"] for a in accounts} == {
        "cash", "investments", "superannuation", "overseas_pensions", "property",
        "vehicles", "loans_credit",
    }  # fmt: skip
    assert {t for a in accounts for t in a.get("tags", [])}
