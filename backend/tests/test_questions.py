"""Questions and Readiness (ADR-0032 sections 1 to 3).

Unit tests for the pure rating (``core.questions``), integration tests against a real temporary
SQLite database with ``today`` and the Effort level injected (no clock), a Hypothesis property for
the share arithmetic and the Guide drift test. Every name and figure is invented.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Iterator, Sequence
from datetime import date, datetime
from fractions import Fraction
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from finance360.capabilities.ai.redact import Redactor, Vocabulary
from finance360.capabilities.auth import logins
from finance360.capabilities.due import service as due
from finance360.capabilities.household import service as household
from finance360.capabilities.position import accounts, valuations
from finance360.capabilities.questions import ratings, service
from finance360.capabilities.reference import fx_rates
from finance360.config import Settings
from finance360.core import due as core_due
from finance360.core import questions as core
from finance360.core.audit import AuditContext
from finance360.core.catalogue import seed_catalogue
from finance360.core.due import IMPORTANT, OPTIONAL, DueItem, EffortLevel
from finance360.core.errors import NotFoundError, ValidationError
from finance360.core.figures import has_figure
from finance360.core.models.answer_rating import AnswerRating
from finance360.core.models.audit import AuditEvent
from finance360.core.money import BASE_CURRENCY, Money
from finance360.core.questions import (
    COULD_NOT_CHECK,
    INCOMPLETE,
    MISSING,
    NOT_READY,
    NOT_STARTED,
    PARTLY_READY,
    READY,
    STALE,
    InputResult,
    InputState,
    Question,
    QuestionInput,
    Reason,
)
from finance360.main import create_app, register_due_rules, register_questions
from support import TestClient, owner_client

CTX = AuditContext("owner", "login:1", "api")
TODAY = date(2026, 11, 3)
LONG_AGO = date(2020, 1, 1)
REPO = Path(__file__).resolve().parents[2]
GUIDE = REPO / "frontend" / "src" / "guide" / "02-what-finance360-answers.md"
MANUAL = "position.manual_valuation"


def aud(amount: str) -> Money:
    whole, _, cents = amount.partition(".")
    return Money(int(whole) * 100 + int((cents + "00")[:2]), BASE_CURRENCY)


def reason(
    kind: core.ReasonKind = STALE, affected: str | None = "0.00", subject: str = "x"
) -> Reason:
    return Reason(
        kind=kind,
        subject_key=subject,
        title="Using an old figure",
        effect="A fresh one is more accurate.",
        affected=aud(affected) if affected is not None else None,
        link="/accounts/1",
    )


# --- the pure rating ----------------------------------------------------------------------------

TOTAL = aud("1000.00")  # 5% is 50.00 and 25% is 250.00


def rate(*reasons: Reason, total: Money | None = TOTAL) -> core.Readiness:
    return core.rate_input(InputResult(total=total, reasons=reasons))


@pytest.mark.parametrize(
    ("kind", "affected", "expected"),
    [
        (STALE, "0.00", READY),
        (STALE, "50.00", READY),  # exactly 5%: not above it
        (STALE, "50.01", PARTLY_READY),
        (INCOMPLETE, "50.00", READY),
        (INCOMPLETE, "50.01", PARTLY_READY),
        (MISSING, "50.00", READY),
        (MISSING, "50.01", PARTLY_READY),
        (MISSING, "250.00", PARTLY_READY),  # exactly 25%: not above it
        (MISSING, "250.01", NOT_READY),
        # a stale or incomplete reason is never Not ready, however large
        (STALE, "250.01", PARTLY_READY),
        (STALE, "1000.00", PARTLY_READY),
        (INCOMPLETE, "1000.00", PARTLY_READY),
    ],
)
def test_the_thresholds_hold_at_exactly_5_and_25_percent(
    kind: core.ReasonKind, affected: str, expected: core.Readiness
) -> None:
    assert rate(reason(kind, affected)) == expected


def test_stale_shares_across_several_accounts_sum_to_exactly_5_percent_and_stay_ready() -> None:
    parts = ("10.00", "15.00", "25.00")  # 50.00 in all: exactly 5% of 1000.00
    assert rate(*(reason(STALE, a, f"a{n}") for n, a in enumerate(parts))) == READY
    one_cent_more = ("10.00", "15.00", "25.01")
    assert rate(*(reason(STALE, a, f"a{n}") for n, a in enumerate(one_cent_more))) == PARTLY_READY


def test_stale_and_incomplete_reasons_are_one_group() -> None:
    assert rate(reason(STALE, "30.00"), reason(INCOMPLETE, "20.00", "b")) == READY  # 5% exactly
    assert rate(reason(STALE, "30.00"), reason(INCOMPLETE, "20.01", "b")) == PARTLY_READY


def test_missing_shares_sum_to_exactly_25_percent_then_one_cent_more_is_not_ready() -> None:
    assert rate(reason(MISSING, "100.00"), reason(MISSING, "150.00", "b")) == PARTLY_READY
    assert rate(reason(MISSING, "100.00"), reason(MISSING, "150.01", "b")) == NOT_READY


def test_a_stale_sum_never_gives_not_ready() -> None:
    many = [reason(STALE, "300.00", f"a{n}") for n in range(3)]  # 90% of the total
    assert rate(*many) == PARTLY_READY


def test_a_missing_and_a_stale_reason_at_3_percent_each_do_not_blend() -> None:
    assert rate(reason(MISSING, "30.00"), reason(STALE, "30.00", "b")) == READY


def test_a_reason_with_an_unknown_value_adds_nothing_to_a_sum() -> None:
    # 50.00 known is exactly 5%; the unknown one adds no cent, so the sum stays at 5%
    unknown = reason(STALE, None, "u")
    result = InputResult(total=TOTAL, reasons=(reason(STALE, "50.00"), unknown))
    assert core.group_sum(result.reasons, STALE, BASE_CURRENCY) == aud("50.00")
    # it is still itself a reason to say Partly ready, and it is material
    assert core.rate_input(result) == PARTLY_READY
    assert core.material_reasons(result) == (unknown,)


def test_a_reason_with_an_unknown_value_is_at_most_partly_ready() -> None:
    for kind in (MISSING, STALE, INCOMPLETE):
        assert rate(reason(kind, None)) == PARTLY_READY
    # and so is one the total cannot weigh (no value to compare with)
    assert rate(reason(MISSING, "10.00"), total=None) == PARTLY_READY
    assert rate(reason(MISSING, "10.00"), total=aud("0.00")) == PARTLY_READY


def test_material_reasons_make_a_group_above_5_percent_count_whole_and_small_ones_not() -> None:
    small_stale = [reason(STALE, "20.00", "s1"), reason(STALE, "20.00", "s2")]  # 4%
    big_missing = reason(MISSING, "60.00", "m1")  # 6% on its own
    result = InputResult(total=TOTAL, reasons=(*small_stale, big_missing))
    assert core.material_reasons(result) == (big_missing,)
    # add up to above 5%: both stale reasons are now why, the larger first
    tipped = InputResult(
        total=TOTAL, reasons=(reason(STALE, "20.00", "s1"), reason(STALE, "31.00", "s2"))
    )
    assert [r.subject_key for r in core.material_reasons(tipped)] == ["s2", "s1"]
    assert core.material_reasons(InputResult(total=TOTAL, reasons=tuple(small_stale))) == ()


def test_an_input_is_not_started_when_empty_and_not_ready_when_blocking() -> None:
    assert core.rate_input(InputResult(empty=True, reasons=(reason(MISSING, None),))) == NOT_STARTED
    assert core.rate_input(InputResult(blocking=True)) == NOT_READY
    assert core.rate_input(InputResult(total=TOTAL)) == READY
    mixed = InputResult(total=TOTAL, reasons=(reason(STALE, "10.00"), reason(STALE, "60.00")))
    assert core.rate_input(mixed) == PARTLY_READY
    # a Not started or blocking input shows its reasons whole
    only = InputResult(empty=True, reasons=(reason(MISSING, None),))
    assert core.material_reasons(only) == only.reasons


@given(
    amounts=st.lists(st.integers(min_value=0, max_value=10**9), max_size=8),
    kinds=st.lists(st.sampled_from([MISSING, STALE, INCOMPLETE]), min_size=8, max_size=8),
    total=st.integers(min_value=1, max_value=10**10),
)
def test_the_sum_rule_equals_fraction_arithmetic(
    amounts: list[int], kinds: list[core.ReasonKind], total: int
) -> None:
    reasons = tuple(
        Reason(
            kind=k,
            subject_key=f"r{n}",
            title="t",
            effect="e",
            affected=Money(a, BASE_CURRENCY),
            link="/a",
        )
        for n, (a, k) in enumerate(zip(amounts, kinds, strict=False))
    )
    missing = Fraction(sum(a for a, k in zip(amounts, kinds, strict=False) if k == MISSING), total)
    old = Fraction(sum(a for a, k in zip(amounts, kinds, strict=False) if k != MISSING), total)
    material, misleading = Fraction(5, 100), Fraction(25, 100)
    if missing > misleading:
        expected = NOT_READY
    elif missing > material or old > material:
        expected = PARTLY_READY
    else:
        expected = READY
    result = InputResult(total=Money(total, BASE_CURRENCY), reasons=reasons)
    assert core.rate_input(result) == expected


@given(
    affected=st.integers(min_value=0, max_value=10**12),
    total=st.integers(min_value=1, max_value=10**12),
    share_ppm=st.sampled_from([core.MATERIAL_SHARE_PPM, core.MISLEADING_SHARE_PPM]),
)
def test_a_share_is_compared_exactly_in_integers(affected: int, total: int, share_ppm: int) -> None:
    expected = Fraction(affected, total) > Fraction(share_ppm, core.PPM)
    got = core.exceeds(Money(affected, BASE_CURRENCY), Money(total, BASE_CURRENCY), share_ppm)
    assert got is expected


def states(*pairs: tuple[str, core.Readiness]) -> list[InputState]:
    return [InputState(i, s) for i, s in pairs]  # type: ignore[arg-type]


def test_a_questions_state_combines_its_inputs_in_the_adr_order() -> None:
    assert core.combine(states((IMPORTANT, READY), (IMPORTANT, READY))) == READY
    assert core.combine(states((IMPORTANT, READY), (IMPORTANT, PARTLY_READY))) == PARTLY_READY
    # any Not ready wins over everything
    assert core.combine(states((IMPORTANT, NOT_READY), (IMPORTANT, COULD_NOT_CHECK))) == NOT_READY
    # an unchecked input is never assumed fine, even beside a Partly ready one
    assert (
        core.combine(states((IMPORTANT, COULD_NOT_CHECK), (IMPORTANT, PARTLY_READY)))
        == COULD_NOT_CHECK
    )
    assert core.combine(states((IMPORTANT, COULD_NOT_CHECK), (IMPORTANT, READY))) == COULD_NOT_CHECK
    # Not started only when every important input is
    assert core.combine(states((IMPORTANT, NOT_STARTED), (IMPORTANT, NOT_STARTED))) == NOT_STARTED
    # once started, an important input still Not started counts as Partly ready
    assert core.combine(states((IMPORTANT, READY), (IMPORTANT, NOT_STARTED))) == PARTLY_READY
    # an optional input that has not started asks for nothing
    assert core.combine(states((IMPORTANT, READY), (OPTIONAL, NOT_STARTED))) == READY
    assert core.combine(states((IMPORTANT, NOT_STARTED), (OPTIONAL, READY))) == NOT_STARTED


def test_the_next_step_orders_missing_before_stale_before_incomplete_then_larger_first() -> None:
    reasons = [
        reason(INCOMPLETE, "900.00", "incomplete"),
        reason(STALE, "100.00", "small-stale"),
        reason(STALE, None, "unknown-stale"),
        reason(STALE, "300.00", "big-stale"),
        reason(MISSING, None, "unknown-missing"),
        reason(MISSING, "20.00", "small-missing"),
    ]
    assert [r.subject_key for r in core.ordered(reasons)] == [
        "small-missing",
        "unknown-missing",
        "big-stale",
        "small-stale",
        "unknown-stale",
        "incomplete",
    ]


def test_the_headline_is_a_sentence_from_the_states_with_no_number_for_readiness() -> None:
    rows: Sequence[tuple[str, core.Readiness]] = [
        ("What do we own and owe?", READY),
        ("Are we on track for retirement?", PARTLY_READY),
        ("How much tax will we pay?", READY),
    ]
    assert core.headline(rows) == (
        "2 of 3 questions are ready; Are we on track for retirement? is partly ready"
    )
    all_ready: list[tuple[str, core.Readiness]] = [("A?", READY)] * 3
    assert core.headline(all_ready) == "All 3 questions are ready."
    mixed: list[tuple[str, core.Readiness]] = [
        ("A?", READY),
        ("B?", NOT_READY),
        ("C?", NOT_STARTED),
    ]
    assert core.headline(mixed) == (
        "1 of 3 questions is ready; B? is not ready, C? has not been started"
    )
    unchecked: list[tuple[str, core.Readiness]] = [("A?", COULD_NOT_CHECK)]
    assert "could not be checked" in core.headline(unchecked)


def test_a_question_needs_inputs_and_ratings_is_a_reserved_key() -> None:
    with pytest.raises(ValueError, match="reserved"):
        Question("ratings", "W?", "/x", "Area", ("a",), "Ask words")
    with pytest.raises(ValueError, match="at least one input"):
        Question("k", "W?", "/x", "Area", (), "Ask words")
    with pytest.raises(ValueError, match="app path"):
        Reason(STALE, "x", "t", "e", None, "https://elsewhere")


# --- the registry as the composition root builds it ---------------------------------------------


@pytest.fixture(autouse=True)
def registered() -> Iterator[None]:
    """The real Due rules, Questions and inputs, registered as ``main.py`` does."""
    rules, questions, inputs = dict(core_due._RULES), dict(core._QUESTIONS), dict(core._INPUTS)
    core_due._RULES.clear()
    core._QUESTIONS.clear()
    core._INPUTS.clear()
    register_due_rules()
    register_questions()
    yield
    core_due._RULES.clear()
    core_due._RULES.update(rules)
    core._QUESTIONS.clear()
    core._QUESTIONS.update(questions)
    core._INPUTS.clear()
    core._INPUTS.update(inputs)


def test_the_three_questions_register_in_card_order_with_registered_inputs() -> None:
    questions = core.questions()
    assert [q.key for q in questions] == [
        "own-and-owe",
        "on-track-for-retirement",
        "tax-this-year",
    ]
    for question in questions:
        for key in question.inputs:
            assert core.get_input(key) is not None, f"{question.key}: {key} is not registered"
    shared = [set(q.inputs) for q in questions[:2]]
    assert {"position.figures"} <= shared[0] & shared[1]
    assert "position.freshness" not in shared[1]  # retirement weighs only what it uses
    assert "retirement.freshness" in shared[1] - shared[0]


def test_no_ask_prompt_carries_a_figure_or_a_name() -> None:
    """ADR-0032 section 4: a static string, so nothing reaches Ask that Ask does not admit."""
    for question in core.questions():
        assert not re.search(r"\d", question.ask_prompt), question.ask_prompt
        assert not re.search(r"[$£€]", question.ask_prompt)
        assert "Sample" not in question.ask_prompt


def test_reasons_never_say_overdue_late_or_failed() -> None:
    """Wording is about accuracy (ADR-0020): checked on every reason the real inputs can give."""
    forbidden = {"overdue", "late", "missed", "behind", "failed"}
    for question_input in core.inputs():
        assert forbidden.isdisjoint(question_input.name.lower().split())


# --- evaluation against a real database ---------------------------------------------------------


class Db:
    def __init__(self, factory: sessionmaker[Session], *, member: bool = True) -> None:
        self.factory = factory
        with factory.begin() as s:
            household.ensure_household(s)
            seed_catalogue(s)
            self.member = (
                household.add_member(s, CTX, "Alex Sample", date(1984, 3, 12)).id if member else 0
            )

    def account(
        self,
        category: str,
        type_key: str,
        *,
        name: str = "Sample account",
        value: str | None = None,
        as_at: date | None = None,
    ) -> int:
        with self.factory.begin() as s:
            data = accounts.AccountInput(
                name=name,
                category_key=category,
                account_type_key=type_key,
                owners=[accounts.OwnerInput(self.member)],
            )
            account_id = accounts.create_account(s, CTX, data).id
            if value is not None:
                self.value(account_id, as_at or TODAY, value, s)
            return account_id

    def value(self, account_id: int, as_at: date, amount: str, s: Session | None = None) -> None:
        def add(session: Session) -> None:
            data = valuations.ValuationInput(as_at, amount, "statement", "Invented statement")
            valuations.add_valuation(session, CTX, account_id, data, today=TODAY)

        if s is not None:
            add(s)
        else:
            with self.factory.begin() as session:
                add(session)

    def ask(
        self, level: EffortLevel = "steady", today: date = TODAY
    ) -> dict[str, service.QuestionView]:
        with self.factory() as s:
            result = service.evaluate(s, today=today, level=level)
        return {v.question.key: v for v in result.questions}


@pytest.fixture
def db(session_factory: sessionmaker[Session]) -> Db:
    return Db(session_factory)


def test_an_empty_household_is_not_started_and_not_red(
    session_factory: sessionmaker[Session],
) -> None:
    views = Db(session_factory).ask()
    own, retirement, tax = (
        views[k] for k in ("own-and-owe", "on-track-for-retirement", "tax-this-year")
    )
    assert own.state == NOT_STARTED
    assert own.count == 3  # every input is still to be given
    assert own.next_step is not None
    assert (own.next_step.input_key, own.next_step.reason.link) == (
        "position.accounts",
        "/accounts",
    )
    assert own.reason == "No Accounts have been added yet"
    assert tax.state == NOT_STARTED
    assert tax.next_step is not None and tax.next_step.reason.link == "/tax/records"
    # a Member alone starts the projection's inputs, so what is left to give shows as Partly ready
    assert retirement.state == PARTLY_READY


def test_a_household_with_no_member_cannot_run_the_projection(
    session_factory: sessionmaker[Session],
) -> None:
    retirement = Db(session_factory, member=False).ask()["on-track-for-retirement"]
    assert retirement.state == NOT_READY  # blocking, not started
    assert retirement.next_step is not None
    assert retirement.next_step.reason.link == "/household"


def test_fresh_figures_are_ready_and_say_nothing_needs_doing(db: Db) -> None:
    db.account("cash", "savings_account", value="1000.00")
    views = db.ask()
    assert views["own-and-owe"].state == READY
    assert views["own-and-owe"].count == 0
    assert views["own-and-owe"].next_step is None
    assert views["own-and-owe"].reason == service.READY_REASON
    assert views["on-track-for-retirement"].state == READY


def test_a_stale_figure_alone_is_partly_ready_never_not_ready(db: Db) -> None:
    """Every figure is old, so the old one is the whole answer: still not red (ADR-0032 s2)."""
    loan = db.account(
        "loans_credit", "home_loan", name="Sample Loan", value="1000.00", as_at=LONG_AGO
    )
    view = db.ask()["own-and-owe"]
    assert view.state == PARTLY_READY
    assert view.count == 1
    assert view.next_step is not None
    assert (view.next_step.input_key, view.next_step.reason.kind) == ("position.freshness", STALE)
    assert view.next_step.reason.link == f"/accounts/{loan}"


def test_a_small_stale_account_does_not_lower_a_question_when_most_value_is_fresh(db: Db) -> None:
    db.account("cash", "savings_account", name="Big", value="95000.00")
    db.account("cash", "savings_account", name="Small", value="5000.00", as_at=LONG_AGO)
    view = db.ask()["own-and-owe"]
    assert view.state == READY  # 5000 of 100000 is exactly 5%, not above it
    assert view.count == 0
    assert view.reason == service.SMALL_GAPS_REASON
    freshness = next(i for i in view.inputs if i.key == "position.freshness")
    assert len(freshness.reasons) == 1  # shown as a small gap, never dropped
    assert freshness.material == ()


def test_a_stale_account_just_above_5_percent_makes_the_question_partly_ready(db: Db) -> None:
    db.account("cash", "savings_account", name="Big", value="94999.99")
    db.account("cash", "savings_account", name="Small", value="5000.00", as_at=LONG_AGO)
    assert db.ask()["own-and-owe"].state == PARTLY_READY  # 5000 of 99999.99


def test_a_stale_loan_or_property_lowers_own_and_owe_but_not_retirement(db: Db) -> None:
    db.account("cash", "savings_account", name="Cash", value="100000.00")
    db.account("superannuation", "super_fund_account", name="Super", value="100000.00")
    db.account("loans_credit", "home_loan", name="Sample Loan", value="100000.00", as_at=LONG_AGO)
    db.account("property", "principal_residence", name="House", value="500000.00", as_at=LONG_AGO)
    views = db.ask()
    assert views["own-and-owe"].state == PARTLY_READY
    retirement = views["on-track-for-retirement"]
    assert retirement.state == READY
    freshness = next(i for i in retirement.inputs if i.key == "retirement.freshness")
    assert freshness.reasons == ()  # the loan and the house are not in what it uses


@pytest.mark.parametrize(
    ("category", "type_key"),
    [("cash", "savings_account"), ("superannuation", "super_fund_account")],
)
def test_a_stale_cash_or_super_account_lowers_both_questions(
    db: Db, category: str, type_key: str
) -> None:
    db.account(category, type_key, name="Stale one", value="100000.00", as_at=LONG_AGO)
    db.account("investments", "brokerage_account", name="Fresh", value="100000.00")
    views = db.ask()
    assert views["own-and-owe"].state == PARTLY_READY
    assert views["on-track-for-retirement"].state == PARTLY_READY
    step = views["on-track-for-retirement"].next_step
    assert step is not None and step.input_key == "retirement.freshness"


def test_many_small_stale_accounts_add_up_to_lower_a_question(db: Db) -> None:
    db.account("investments", "brokerage_account", name="Big", value="90000.00")
    for n in range(3):  # 3 x 2,000.00 stale is 6% of 96,000.00; each alone is 2.1%
        db.account("cash", "savings_account", name=f"Small {n}", value="2000.00", as_at=LONG_AGO)
    view = db.ask()["own-and-owe"]
    assert view.state == PARTLY_READY
    freshness = next(i for i in view.inputs if i.key == "position.freshness")
    assert len(freshness.material) == 3  # together they are why


def test_an_account_in_another_currency_with_no_rate_is_missing_with_an_unknown_value(
    db: Db,
) -> None:
    """Its figure cannot be counted, and with no rate it has no AUD value to weigh."""
    db.account("cash", "savings_account", name="Counted", value="75000.00")
    with db.factory.begin() as s:
        data = accounts.AccountInput(
            name="Sample UK account",
            category_key="cash",
            account_type_key="savings_account",
            owners=[accounts.OwnerInput(db.member)],
            currency="GBP",
        )
        uk = accounts.create_account(s, CTX, data).id
        db.value(uk, TODAY, "25000.00", s)
    view = db.ask()["own-and-owe"]
    assert view.state == PARTLY_READY  # unknown value: at most Partly ready, never red
    assert view.next_step is not None
    step = view.next_step
    assert (step.input_key, step.reason.kind, step.reason.affected) == (
        "position.figures",
        MISSING,
        None,
    )
    assert step.reason.link == f"/accounts/{uk}"


@pytest.mark.parametrize(
    ("affected", "expected"),
    [("25000.00", PARTLY_READY), ("25000.01", NOT_READY)],  # exactly 25% is not above it
)
def test_a_question_goes_not_ready_when_missing_value_passes_25_percent_of_the_total(
    db: Db, affected: str, expected: core.Readiness
) -> None:
    gap = Reason(
        MISSING, "gap", "Leaves out an Account", "Adding it helps.", aud(affected), "/accounts"
    )
    total = aud("100000.00")
    core.register_input(
        QuestionInput(
            "test.gap",
            "Gap",
            IMPORTANT,
            (),
            lambda *_a: InputResult(total=total, reasons=(gap,)),
        )
    )
    core.register_question(Question("test-q", "Test?", "/x", "Area", ("test.gap",), "Ask"))
    view = db.ask()["test-q"]
    assert view.state == expected
    assert core.MISLEADING_SHARE_PPM == 250_000  # the named constant, held as parts per million
    assert view.next_step is not None and view.next_step.reason is gap


def test_an_account_with_no_figure_and_no_last_known_value_is_at_most_partly_ready(db: Db) -> None:
    db.account("cash", "savings_account", name="Counted", value="1000.00")
    db.account("cash", "savings_account", name="Never valued")  # no Valuation at all
    view = db.ask()["own-and-owe"]
    assert view.state == PARTLY_READY
    assert view.next_step is not None
    assert view.next_step.reason.kind == MISSING and view.next_step.reason.affected is None


def test_light_is_not_lowered_for_what_it_does_not_ask(db: Db) -> None:
    """A stale property Valuation is an optional Due rule, not asked at Light."""
    db.account("cash", "savings_account", name="Cash", value="1000.00")
    db.account("property", "principal_residence", name="House", value="900000.00", as_at=LONG_AGO)
    assert db.ask("light")["own-and-owe"].state == READY
    assert db.ask("steady")["own-and-owe"].state == PARTLY_READY
    assert db.ask("thorough")["own-and-owe"].state == PARTLY_READY


def test_a_snoozed_item_is_not_counted(db: Db) -> None:
    db.account("cash", "savings_account", name="Cash", value="1000.00", as_at=LONG_AGO)
    assert db.ask()["own-and-owe"].state == PARTLY_READY
    with db.factory() as s:
        item = due.whats_due(s, today=TODAY, level="steady").items[0]
    with db.factory.begin() as s:
        due.snooze(s, CTX, item.rule_key, item.subject_key, "month", today=TODAY)
    view = db.ask()["own-and-owe"]
    assert view.state == READY
    assert view.count == 0


def test_an_optional_input_is_ignored_at_light_and_counts_otherwise(
    session_factory: sessionmaker[Session],
) -> None:
    db = Db(session_factory)
    nag = Reason(STALE, "nag", "An old extra figure", "A fresh one helps.", None, "/accounts")
    core.register_input(
        QuestionInput("test.extra", "Extras", OPTIONAL, (), lambda *_a: InputResult(reasons=(nag,)))
    )
    core.register_input(
        QuestionInput("test.core", "Core", IMPORTANT, (), lambda *_a: InputResult())
    )
    core.register_question(
        Question("test-q", "Test?", "/x", "Area", ("test.core", "test.extra"), "Ask")
    )
    assert db.ask("light")["test-q"].state == READY
    assert db.ask("light")["test-q"].count == 0
    assert db.ask("steady")["test-q"].state == PARTLY_READY


def test_a_check_that_raises_is_could_not_check_never_ready_and_the_rest_still_answers(
    session_factory: sessionmaker[Session],
) -> None:
    db = Db(session_factory)
    db.account("cash", "savings_account", value="1000.00")

    def boom(*_args: object) -> InputResult:
        raise RuntimeError("a check failed")

    core.register_input(QuestionInput("test.boom", "Broken input", IMPORTANT, (), boom))
    core.register_input(
        QuestionInput("test.fine", "Fine input", IMPORTANT, (), lambda *_a: InputResult())
    )
    core.register_question(
        Question("test-q", "Test?", "/x", "Area", ("test.fine", "test.boom"), "Ask")
    )
    views = db.ask()
    broken = views["test-q"]
    assert broken.state == COULD_NOT_CHECK
    assert broken.count == 1
    assert broken.reason == "Could not check Broken input."
    assert broken.next_step is None
    assert views["own-and-owe"].state == READY  # one failure never blanks the others
    with db.factory() as s:
        headline = service.evaluate(s, today=TODAY, level="steady").headline
    assert "Test? could not be checked" in headline


def test_a_due_rule_that_cannot_be_checked_makes_the_input_that_weighs_it_could_not_check(
    db: Db,
) -> None:
    db.account("cash", "savings_account", value="1000.00")
    original = core_due.get(MANUAL)
    assert original is not None

    def broken(*_args: object) -> tuple[DueItem, ...]:
        raise RuntimeError("the rule failed")

    core_due.register(
        core_due.DueRule(
            original.key,
            original.importance,
            original.cadence,
            original.suits,
            original.guide_item,
            original.interested,
            broken,
        )
    )
    view = db.ask()["own-and-owe"]
    assert view.state == COULD_NOT_CHECK
    assert view.reason == "Could not check Fresh balances."


def test_an_unregistered_input_is_could_not_check(db: Db) -> None:
    core.register_question(Question("test-q", "Test?", "/x", "Area", ("test.nothing",), "Ask"))
    assert db.ask()["test-q"].state == COULD_NOT_CHECK


def test_readiness_is_never_stored_it_follows_the_data(db: Db) -> None:
    account = db.account("cash", "savings_account", value="1000.00", as_at=LONG_AGO)
    assert db.ask()["own-and-owe"].state == PARTLY_READY
    db.value(account, TODAY, "1001.00")  # recording a figure is the whole fix
    assert db.ask()["own-and-owe"].state == READY


# --- the sample household -----------------------------------------------------------------------


@pytest.fixture
def sample(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(REPO / "fixtures" / "sample_household" / "household.json", data / "household.json")
    settings = Settings(data_dir=data, port=8000, git_sha="dev", static_dir=None)
    with owner_client(create_app(settings)) as client:
        yield client


def sample_states(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, today: date
) -> dict[str, dict[str, object]]:
    monkeypatch.setattr(due, "due_today", lambda _s: today)
    body = client.get("/api/questions").json()
    assert body["today"] == today.isoformat()
    return {q["key"]: q for q in body["questions"]}


def test_the_sample_household_is_ready_while_its_figures_are_fresh_and_small_gaps_stay_small(
    sample: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mid-October 2026: every material Valuation is under a month old. The dormant Accounts are
    years old but worth nothing, so they are small gaps and the Questions stay Ready."""
    got = sample_states(sample, monkeypatch, date(2026, 10, 15))
    assert got["own-and-owe"]["state"] == "ready"
    assert got["own-and-owe"]["count"] == 0
    assert got["on-track-for-retirement"]["state"] == "ready"


def test_the_sample_household_is_partly_ready_when_its_loan_and_super_balances_age(
    sample: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    got = sample_states(sample, monkeypatch, date(2026, 11, 27))
    own = got["own-and-owe"]
    assert (own["state"], own["state_word"], own["count"]) == ("partly_ready", "Partly ready", 1)
    step = own["next_step"]
    assert isinstance(step, dict)
    assert step["input_key"] == "position.freshness" and step["kind"] == "stale"
    assert "Sample Home Loan" in step["title"]  # the largest of the two material gaps
    assert str(step["link"]).startswith("/accounts/")
    # retirement is not asserted here: it depends on the real load date (see the test below)


@pytest.mark.parametrize("load_day", [date(2026, 12, 10), date(2027, 6, 1), date(2027, 9, 1)])
def test_the_sample_household_is_partly_ready_and_ready_on_the_day_it_is_loaded(
    session_factory: sessionmaker[Session], tmp_path: Path, load_day: date
) -> None:
    """The review slot loads the fixture on the real date: own-and-owe Partly ready (the home loan
    is a stale Valuation the projection does not use) and retirement Ready, whatever that date is
    (ADR-0032 section 8; the fixture's Valuations of the Accounts the projection uses are dated
    relative to the load date)."""
    shutil.copy(REPO / "fixtures" / "sample_household" / "household.json", tmp_path)
    with session_factory.begin() as s:
        seed_catalogue(s)
        household.bootstrap(s, tmp_path)
        assert accounts.load_fixture_accounts(s, tmp_path)
        valuations.load_fixture_valuations(s, tmp_path, today=load_day)
        fx_rates.load_fixture_fx_rates(s, tmp_path)
    with session_factory() as s:
        views = {v.question.key: v for v in service.evaluate(s, today=load_day).questions}
    own, retirement = views["own-and-owe"], views["on-track-for-retirement"]
    assert (own.state, retirement.state) == (PARTLY_READY, READY)
    assert own.next_step is not None
    stale = next(i for i in own.inputs if i.key == "position.freshness")
    assert any("Sample Home Loan" in r.title for r in stale.material)
    retirement_inputs = {i.key: i for i in retirement.inputs}
    assert retirement_inputs["position.figures"].reasons == ()  # nothing without a figure
    assert retirement_inputs["retirement.freshness"].material == ()
    # recording a fresh Valuation of the loan turns the first one Ready
    loan = next(i for i in own.inputs if i.key == "position.freshness").material[0].link
    with session_factory.begin() as s:
        valuations.add_valuation(
            s,
            CTX,
            int(loan.rsplit("/", 1)[1]),
            valuations.ValuationInput(load_day, "496000.00", "statement", "Invented statement"),
            today=load_day,
        )
    with session_factory() as s:
        again = {v.question.key: v for v in service.evaluate(s, today=load_day).questions}
    assert again["own-and-owe"].state == READY


def test_the_sample_households_tax_year_waits_for_rule_figures_the_pack_does_not_have_yet(
    sample: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shipped Rule pack holds FY 2025-26 only, so the current year cannot be estimated: Not
    ready, with the fix one click away on the Rule figures screen."""
    tax = sample_states(sample, monkeypatch, date(2026, 11, 27))["tax-this-year"]
    assert tax["state"] == "not_ready"
    step = tax["next_step"]
    assert isinstance(step, dict)
    assert step["link"] == "/rule-figures?fy=2027&missing=1"
    # the year before the pack's gap starts is estimable: nothing in the way but what is unmodelled
    earlier = sample_states(sample, monkeypatch, date(2026, 6, 20))["tax-this-year"]
    assert earlier["state"] in {"ready", "partly_ready"}


def test_the_api_has_no_number_for_readiness(sample: TestClient) -> None:
    body = sample.get("/api/questions").json()
    assert set(body) == {"today", "level", "headline", "questions"}
    for question in body["questions"]:
        assert isinstance(question["state"], str)
        assert not {"score", "percent", "percentage", "points"} & set(question)


# --- the Guide cannot drift from the registry ---------------------------------------------------


def test_every_registered_question_is_a_bold_lead_on_the_answers_page_linking_its_page() -> None:
    """ADR-0032 section 8: the page stays prose; its bold leads are the Questions' wording, on a
    line that links the Question page, and every such link has a registered Question."""
    text = GUIDE.read_text(encoding="utf-8")
    lines = text.splitlines()
    for question in core.questions():
        mine = [line for line in lines if f"**{question.wording}**" in line]
        assert len(mine) == 1, f"{question.key}: expected one Guide line for **{question.wording}**"
        assert f"](/questions/{question.key})" in mine[0], f"{question.key}: link its page"
    linked = set(re.findall(r"\]\(/questions/([^)\s]+)\)", text))
    assert linked, "the answers page links no Question page"
    assert linked <= {q.key for q in core.questions()}, linked - {q.key for q in core.questions()}


# --- one Question: the page behind a card -------------------------------------------------------


def test_one_question_is_the_same_evaluation_as_its_card_with_every_input_in_order(
    sample: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The page lists every input the card counts, in the registry's order, with the same states."""
    cards = sample_states(sample, monkeypatch, date(2026, 11, 27))
    for question in core.questions():
        card = cards[question.key]
        page = sample.get(f"/api/questions/{question.key}").json()
        assert {k: page[k] for k in card} == card  # the card's own fields, unchanged
        assert [i["key"] for i in page["inputs"]] == list(question.inputs)
        assert page["count"] == sum(1 for i in page["inputs"] if i["counted"])
        for i in page["inputs"]:
            assert i["state_word"] == core.STATE_WORDS[i["state"]]
            assert all(r["link"].startswith("/") for r in i["reasons"])
        step = page["next_step"]
        if step is not None:  # the card's next step is the first material reason of its input
            owner = next(i for i in page["inputs"] if i["key"] == step["input_key"])
            first = next(r for r in owner["reasons"] if r["material"])
            assert (first["title"], first["link"]) == (step["title"], step["link"])


def test_the_partly_ready_sample_question_names_the_stale_inputs_with_fix_links(
    sample: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample_states(sample, monkeypatch, date(2026, 11, 27))
    page = sample.get("/api/questions/own-and-owe").json()
    by_key = {i["key"]: i for i in page["inputs"]}
    assert by_key["position.freshness"]["state"] == "partly_ready"
    material = [r for r in by_key["position.freshness"]["reasons"] if r["material"]]
    assert material and all(r["kind"] == "stale" for r in material)
    assert by_key["position.accounts"]["state"] == "ready"
    assert by_key["position.accounts"]["counted"] is False


def test_an_unknown_question_is_a_404_and_ratings_is_not_a_question(sample: TestClient) -> None:
    assert sample.get("/api/questions/no-such-question").status_code == 404
    # ``ratings`` is routed to the ratings list, never read as a Question key.
    assert sample.get("/api/questions/ratings").json()["items"][0]["key"] == "own-and-owe"


def test_an_optional_input_is_shown_but_not_asked_at_light(db: Db) -> None:
    def stub(_s: Session, _t: date, _l: EffortLevel, _i: Sequence[DueItem]) -> InputResult:
        return InputResult(reasons=(reason(STALE, "900.00"),), total=aud("1000.00"))

    core.register_input(
        QuestionInput("t.optional", "Optional thing", OPTIONAL, (), stub),
    )
    core.register_input(
        QuestionInput("t.main", "Main thing", IMPORTANT, (), lambda *_: InputResult())
    )
    core.register_question(
        Question("t-q", "Test?", "/position", "Where we stand", ("t.main", "t.optional"), "Words")
    )
    try:
        for level, asked, count in (("light", False, 0), ("steady", True, 1)):
            with db.factory() as s:
                view = service.evaluate_one(s, "t-q", today=TODAY, level=level)  # type: ignore[arg-type]
            optional = view.inputs[1]
            assert (optional.asked, view.count) == (asked, count)
            assert optional.state == PARTLY_READY  # shown with its real state either way
            assert [i.key for i in view.inputs] == ["t.main", "t.optional"]
    finally:
        core.unregister_question("t-q")
        core.unregister_input("t.optional")
        core.unregister_input("t.main")


# --- Answer ratings (ADR-0032 section 6) --------------------------------------------------------


@pytest.fixture
def login_id(session_factory: sessionmaker[Session], fast_hash: None) -> int:
    with session_factory.begin() as s:
        return logins.create_login(s, "synthetic-test-passphrase-1", now=datetime(2026, 1, 1)).id


def rate_now(db: Db, login: int, key: str, answer: str, note: str | None = None) -> ratings.Rating:
    with db.factory.begin() as s:
        return ratings.record_rating(s, login, key, answer, note, today=TODAY, level="steady")


def test_the_newest_rating_is_current_and_earlier_ones_are_kept(db: Db, login_id: int) -> None:
    first = rate_now(db, login_id, "own-and-owe", "no", "I wanted the split by owner")
    second = rate_now(db, login_id, "own-and-owe", "partly")
    rate_now(db, login_id, "tax-this-year", "yes")
    with db.factory() as s:
        history = ratings.rating_history(s, login_id)
        latest = ratings.latest_ratings(s, login_id)
    assert [r.answer for r in history["own-and-owe"]] == ["partly", "no"]  # newest first
    assert latest["own-and-owe"].id == second.id and latest["tax-this-year"].answer == "yes"
    assert set(latest) == {"own-and-owe", "tax-this-year"}  # an unrated Question has no entry
    assert history["own-and-owe"][1] == first  # the earlier row is untouched
    assert second.note is None and first.note == "I wanted the split by owner"
    with db.factory() as s:
        assert s.scalar(select(func.count()).select_from(AnswerRating)) == 3


def test_the_state_word_at_the_time_is_stamped_ready_and_not_ready(db: Db, login_id: int) -> None:
    db.account("cash", "savings_account", name="Cash", value="1000.00")
    assert rate_now(db, login_id, "own-and-owe", "no").readiness_at_rating == READY
    core.register_input(
        QuestionInput(
            "t.gap",
            "A missing figure",
            IMPORTANT,
            (),
            lambda *_: InputResult(reasons=(reason(MISSING, "1000.00"),), total=aud("1000.00")),
        )
    )
    core.register_question(Question("t-q", "Test?", "/position", "Where we stand", ("t.gap",), "W"))
    assert db.ask()["t-q"].state == NOT_READY
    stamped = rate_now(db, login_id, "t-q", "yes")
    assert stamped.readiness_at_rating == NOT_READY
    with db.factory() as s:
        assert ratings.rating_history(s, login_id)["t-q"][0].readiness_at_rating == NOT_READY


def test_a_rating_never_changes_readiness(db: Db, login_id: int) -> None:
    db.account("cash", "savings_account", name="Cash", value="1000.00")
    with db.factory() as s:
        before = service.evaluate(s, today=TODAY, level="steady")
    for answer in ("no", "partly", "yes"):
        rate_now(db, login_id, "own-and-owe", answer, "about the same")
        with db.factory() as s:
            assert service.evaluate(s, today=TODAY, level="steady") == before
    assert before.questions[0].state == READY  # Ready, and rated No: both are true at once


@pytest.mark.parametrize(
    "note",
    [None, "", "   ", "I wanted it by owner", "x" * 200, "12345", "1 2 3", "6 Nov 2026"]
    + ["2026-11-06", "FY 2026-27", "about 5 accounts"],
)
def test_a_plain_note_is_accepted(db: Db, login_id: int, note: str | None) -> None:
    stored = rate_now(db, login_id, "own-and-owe", "partly", note)
    assert stored.note == ((note or "").strip() or None)


@pytest.mark.parametrize(
    ("note", "message"),
    [
        ("x" * 201, ratings.NOTE_TOO_LONG),
        ("it was $5", ratings.NOTE_HAS_FIGURE),
        ("AUD 500 or so", ratings.NOTE_HAS_FIGURE),
        ("about 2k", ratings.NOTE_HAS_FIGURE),
        ("1,200 dollars", ratings.NOTE_HAS_FIGURE),
        ("123456", ratings.NOTE_HAS_FIGURE),
        ("acct 12 34 56", ratings.NOTE_HAS_FIGURE),
        ("ref 123-456-7", ratings.NOTE_HAS_FIGURE),
    ],
)
def test_a_note_with_an_amount_or_six_digits_or_too_long_is_refused_and_nothing_is_stored(
    db: Db, login_id: int, note: str, message: str
) -> None:
    rate_now(db, login_id, "own-and-owe", "yes", "kept")
    with pytest.raises(ValidationError) as caught:
        rate_now(db, login_id, "own-and-owe", "no", note)
    assert [(e.field, e.message) for e in caught.value.errors] == [("note", message)]
    with db.factory() as s:
        rows = ratings.rating_history(s, login_id)["own-and-owe"]
    assert [(r.answer, r.note) for r in rows] == [("yes", "kept")]  # not stored, not edited


def test_the_note_limits_are_exactly_200_characters_and_six_digits(db: Db, login_id: int) -> None:
    assert rate_now(db, login_id, "own-and-owe", "yes", "y" * 200).note == "y" * 200
    with pytest.raises(ValidationError):
        rate_now(db, login_id, "own-and-owe", "yes", "y" * 201)
    rate_now(db, login_id, "own-and-owe", "yes", "12345")
    with pytest.raises(ValidationError):
        rate_now(db, login_id, "own-and-owe", "yes", "123456")


def test_an_unknown_key_ratings_and_an_unknown_answer_are_refused(db: Db, login_id: int) -> None:
    for key in ("no-such-question", "ratings"):
        with pytest.raises(NotFoundError):
            rate_now(db, login_id, key, "yes")
    with pytest.raises(ValidationError) as caught:
        rate_now(db, login_id, "own-and-owe", "maybe")
    assert caught.value.errors[0].field == "answer"
    with db.factory() as s:
        assert s.scalar(select(func.count()).select_from(AnswerRating)) == 0


@given(st.text(alphabet="0123456789 -.,$kmAUDa", max_size=30))
def test_the_note_check_finds_what_redaction_would_replace(text: str) -> None:
    """One definition of a figure (``core.figures``): redaction changes the text exactly when the
    note check refuses it, so the two cannot drift apart."""
    redacted = Redactor(Vocabulary()).redact(text) != text
    assert has_figure(text) == redacted


@pytest.fixture
def api(tmp_path: Path, fast_hash: None) -> Iterator[TestClient]:
    settings = Settings(data_dir=tmp_path, port=8000, git_sha="dev", static_dir=None)
    app = create_app(settings)
    with owner_client(app) as client:
        with app.state.session_factory.begin() as s:
            made = logins.create_login(s, "synthetic-test-passphrase-1", now=datetime(2026, 1, 1))
            assert made.id == 1  # the id the test owner signs in as
        yield client


def test_the_api_rates_a_question_and_reads_it_back_beside_an_unchanged_state(
    api: TestClient,
) -> None:
    before = api.get("/api/questions/own-and-owe").json()
    assert before["rating"] is None and before["earlier_ratings"] == []
    assert all(q["rating"] is None for q in api.get("/api/questions").json()["questions"])

    posted = api.post(
        "/api/questions/own-and-owe/ratings", json={"answer": "no", "note": "Wanted it by owner"}
    )
    assert posted.status_code == 201
    body = posted.json()
    assert (body["answer"], body["answer_word"], body["note"]) == ("no", "No", "Wanted it by owner")
    assert body["readiness_at_rating"] == before["state"]
    assert body["readiness_word"] == before["state_word"]

    api.post("/api/questions/own-and-owe/ratings", json={"answer": "partly"})
    after = api.get("/api/questions/own-and-owe").json()
    assert after["rating"]["answer"] == "partly"
    assert [r["answer"] for r in after["earlier_ratings"]] == ["no"]
    for field in ("state", "state_word", "count", "reason", "next_step", "inputs"):
        assert after[field] == before[field]  # a rating moves nothing about Readiness
    home = {q["key"]: q for q in api.get("/api/questions").json()["questions"]}
    assert home["own-and-owe"]["rating"]["answer"] == "partly"
    assert home["tax-this-year"]["rating"] is None
    listed = api.get("/api/questions/ratings").json()["items"]
    assert {i["key"]: i["rating"] and i["rating"]["answer"] for i in listed} == {
        "own-and-owe": "partly",
        "on-track-for-retirement": None,
        "tax-this-year": None,
    }


def test_a_ready_question_rated_no_shows_both(api: TestClient) -> None:
    detail = api.get("/api/questions/tax-this-year").json()
    api.post("/api/questions/tax-this-year/ratings", json={"answer": "no"})
    after = api.get("/api/questions/tax-this-year").json()
    assert after["state"] == detail["state"]
    assert after["rating"]["answer"] == "no"
    assert after["rating"]["readiness_at_rating"] == detail["state"]


def test_the_api_refuses_a_bad_note_a_bad_answer_and_a_bad_key(api: TestClient) -> None:
    refused = api.post("/api/questions/own-and-owe/ratings", json={"answer": "yes", "note": "$40"})
    assert refused.status_code == 422
    assert refused.json() == {"errors": [{"field": "note", "message": ratings.NOTE_HAS_FIGURE}]}
    bad = api.post("/api/questions/own-and-owe/ratings", json={"answer": "maybe"})
    assert bad.status_code == 422 and bad.json()["errors"][0]["field"] == "answer"
    assert api.post("/api/questions/nope/ratings", json={"answer": "yes"}).status_code == 404
    assert api.post("/api/questions/ratings/ratings", json={"answer": "yes"}).status_code == 404
    assert api.get("/api/questions/own-and-owe").json()["rating"] is None  # nothing was stored


def test_a_rating_writes_no_audit_event(api: TestClient) -> None:
    def audits() -> int:
        with api.app.state.session_factory() as s:  # type: ignore[attr-defined]
            return s.scalar(select(func.count()).select_from(AuditEvent)) or 0

    before = audits()
    assert api.post("/api/questions/own-and-owe/ratings", json={"answer": "yes"}).status_code == 201
    assert audits() == before


def test_ratings_are_not_read_by_ask_or_exposed_over_mcp() -> None:
    """Nothing leaves the instance (ADR-0032 section 6): neither the Ask Capability nor the MCP
    layer so much as names the table or the ratings service."""
    root = Path(__file__).resolve().parents[1] / "finance360"
    for folder in (root / "capabilities" / "ask", root / "mcp"):
        for source in folder.rglob("*.py"):
            text = source.read_text(encoding="utf-8")
            for name in ("answer_rating", "AnswerRating", "questions.ratings", "record_rating"):
                assert name not in text, f"{source.name} mentions {name}"


# --- Snoozed gaps on the Question page ----------------------------------------------------------


def snooze_the_stale_item(db: Db, choice: str) -> None:
    with db.factory() as s:
        item = due.whats_due(s, today=TODAY, level="steady").items[0]
    with db.factory.begin() as s:
        due.snooze(s, CTX, item.rule_key, item.subject_key, choice, today=TODAY)


def test_a_snoozed_gap_is_listed_with_its_end_date_and_still_not_counted(db: Db) -> None:
    db.account("cash", "savings_account", name="Cash", value="1000.00", as_at=LONG_AGO)
    assert db.ask()["own-and-owe"].state == PARTLY_READY
    snooze_the_stale_item(db, "month")
    view = db.ask()["own-and-owe"]
    assert (view.state, view.count) == (READY, 0)  # the Snoozed gap weighs on nothing
    gaps = [g for i in view.inputs for g in i.snoozed]
    assert len(gaps) == 1
    assert gaps[0].until_on == date(2026, 12, 4)  # 31 days from TODAY
    assert "Cash" in gaps[0].title and gaps[0].link.startswith("/accounts/")
    assert [g for i in db.ask()["tax-this-year"].inputs for g in i.snoozed] == []


def test_a_gap_skipped_for_this_period_is_listed_without_a_date(db: Db) -> None:
    db.account("cash", "savings_account", name="Cash", value="1000.00", as_at=LONG_AGO)
    snooze_the_stale_item(db, "skip")
    gaps = [g for i in db.ask()["own-and-owe"].inputs for g in i.snoozed]
    assert [g.until_on for g in gaps] == [None]


def test_a_snoozed_gap_sits_under_the_input_that_weighs_it(db: Db) -> None:
    db.account("cash", "savings_account", name="Cash", value="1000.00", as_at=LONG_AGO)
    snooze_the_stale_item(db, "month")
    snoozed = {i.key: len(i.snoozed) for i in db.ask()["own-and-owe"].inputs}
    assert snoozed["position.freshness"] == 1
    assert snoozed["position.accounts"] == 0
