"""Questions and Readiness (ADR-0032 sections 1 to 3).

Unit tests for the pure rating (``core.questions``), integration tests against a real temporary
SQLite database with ``today`` and the Effort level injected (no clock), a Hypothesis property for
the share arithmetic and the Guide drift test. Every name and figure is invented.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Iterator, Sequence
from datetime import date
from fractions import Fraction
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy.orm import Session, sessionmaker

from finance360.capabilities.due import service as due
from finance360.capabilities.household import service as household
from finance360.capabilities.position import accounts, valuations
from finance360.capabilities.questions import service
from finance360.config import Settings
from finance360.core import due as core_due
from finance360.core import questions as core
from finance360.core.audit import AuditContext
from finance360.core.catalogue import seed_catalogue
from finance360.core.due import IMPORTANT, OPTIONAL, DueItem, EffortLevel
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
    assert core.rate_reason(reason(kind, affected), TOTAL) == expected


def test_a_reason_with_an_unknown_value_is_at_most_partly_ready() -> None:
    for kind in (MISSING, STALE, INCOMPLETE):
        assert core.rate_reason(reason(kind, None), TOTAL) == PARTLY_READY
    # and so is one the total cannot weigh (no value to compare with)
    assert core.rate_reason(reason(MISSING, "10.00"), None) == PARTLY_READY
    assert core.rate_reason(reason(MISSING, "10.00"), aud("0.00")) == PARTLY_READY


def test_an_input_is_not_started_when_empty_and_not_ready_when_blocking() -> None:
    assert core.rate_input(InputResult(empty=True, reasons=(reason(MISSING, None),))) == NOT_STARTED
    assert core.rate_input(InputResult(blocking=True)) == NOT_READY
    assert core.rate_input(InputResult(total=TOTAL)) == READY
    mixed = InputResult(total=TOTAL, reasons=(reason(STALE, "10.00"), reason(STALE, "60.00")))
    assert core.rate_input(mixed) == PARTLY_READY  # the worst reason decides


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
    assert {"position.figures", "position.freshness"} <= shared[0] & shared[1]


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
    assert got["on-track-for-retirement"]["state"] == "partly_ready"


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


def test_every_registered_question_is_a_bold_lead_on_the_answers_page_linking_its_screen() -> None:
    """ADR-0032 section 8: the page stays prose; its bold leads are the Questions' wording. The link
    to the Question page (``/questions/<key>``) arrives with that page (#121); until then each line
    links the screen that answers it."""
    lines = GUIDE.read_text(encoding="utf-8").splitlines()
    for question in core.questions():
        mine = [line for line in lines if f"**{question.wording}**" in line]
        assert len(mine) == 1, f"{question.key}: expected one Guide line for **{question.wording}**"
        assert f"]({question.screen})" in mine[0], f"{question.key}: the line must link its screen"
