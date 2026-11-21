"""Captured Rule figures on a real migrated SQLite database (ADR-0022).

Invariant work (a figure used in money maths, an append-only table): capture, correct and withdraw
restore the right value; one active row per figure; the label follows the set; a saved run keeps a
capture id that still resolves after a withdraw; a capture that no longer fits never raises. Every
value is invented.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from datetime import date

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DatabaseError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from finance360.core import rulepack, rulepack_store
from finance360.core.audit import AuditContext
from finance360.core.errors import NotFoundError, ValidationError
from finance360.core.models.audit import AuditEvent
from finance360.core.models.rule_capture import RuleCapture
from finance360.core.money import Money
from finance360.core.rulepack import CPI, RATE, FigureSpec
from finance360.rulepacks.au import PACK

CTX = AuditContext("owner", "login:1", "api")
TODAY = date(2026, 11, 5)  # FY 2026-27, so FY 2027 and FY 2028 can be captured
RATE_KEY = "tax.car.rate_per_km"
LIMIT_KEY = "tax.car.max_km"
_HELD = PACK.years[2026][RATE_KEY]  # the release's figure for FY 2025-26: read, never asserted
assert isinstance(_HELD, Money)
SHIPPED: Money = _HELD


def typed(value: str, **more: object) -> rulepack_store.CaptureInput:
    return rulepack_store.CaptureInput(value, quote="An invented sentence.", **more)  # type: ignore[arg-type]


def effective(s: Session) -> rulepack.RulePack:
    pack = rulepack_store.effective_pack(s, "au")
    assert pack is not None
    return pack


def capture(s: Session, fy: int, value: str, key: str = RATE_KEY, **more: object) -> RuleCapture:
    return rulepack_store.capture_figure(s, CTX, "au", key, fy, typed(value, **more), today=TODAY)


def test_no_captures_reads_exactly_the_shipped_pack(session_factory: sessionmaker[Session]) -> None:
    # Catches: the effective pack drifting from today's behaviour when nothing is captured.
    with session_factory() as s:
        pack = effective(s)
    assert pack is PACK
    assert pack.version_label == "au/2026-27/r9"


def test_a_capture_supplies_a_year_the_release_lacks_and_a_withdraw_takes_it_back(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as s:
        row = capture(s, 2027, "0.90")
        assert (row.method, row.captured_kind, row.captured_by) == ("typed", "owner", "login:1")
        assert row.retrieved_on == TODAY and row.ended_at is None
    with session_factory() as s:
        pack = effective(s)
        assert pack.years[2027][RATE_KEY] == Money(90, "AUD")
        assert pack.version_label.startswith("au/2026-27/r9+1:")
        assert pack.capture_of(2027, RATE_KEY).id == row.id  # type: ignore[union-attr]
    with session_factory.begin() as s:
        rulepack_store.withdraw_figure(s, CTX, row.id)
    with session_factory() as s:
        pack = effective(s)
        assert RATE_KEY not in pack.years.get(2027, {})
        assert pack is PACK and pack.version_label == "au/2026-27/r9"


def test_correcting_supersedes_and_a_withdraw_restores_the_release_figure_not_the_old_capture(
    session_factory: sessionmaker[Session],
) -> None:
    # Catches: a withdraw reviving the superseded capture instead of falling back to the release.
    first_cents, second_cents = SHIPPED.amount_minor + 3, SHIPPED.amount_minor + 5
    with session_factory.begin() as s:
        first = capture(s, 2026, f"{first_cents / 100:.2f}")
        first_id = first.id
    with session_factory.begin() as s:
        second = rulepack_store.correct_figure(
            s, CTX, first_id, typed(f"{second_cents / 100:.2f}"), today=TODAY
        )
        second_id = second.id
    with session_factory() as s:
        old, new = s.get(RuleCapture, first_id), s.get(RuleCapture, second_id)
        assert old is not None and new is not None
        assert (old.ended_kind, old.superseded_by_id) == ("superseded", second_id)
        assert old.ended_by == "login:1" and new.ended_at is None
        assert effective(s).years[2026][RATE_KEY] == Money(second_cents, "AUD")
        assert effective(s).capture_of(2026, RATE_KEY).shipped_value == SHIPPED  # type: ignore[union-attr]
    with session_factory.begin() as s:
        ended = rulepack_store.withdraw_figure(s, CTX, second_id)
        assert ended.ended_kind == "withdrawn" and ended.superseded_by_id is None
    with session_factory() as s:
        assert effective(s).years[2026][RATE_KEY] == SHIPPED
        assert effective(s) is PACK
        rows = s.scalars(select(RuleCapture).order_by(RuleCapture.id)).all()
        assert [r.ended_kind for r in rows] == ["superseded", "withdrawn"]  # kept, never deleted


def test_there_is_one_active_row_per_figure(session_factory: sessionmaker[Session]) -> None:
    # Catches: two active captures of one figure (which would make the merge order matter).
    with session_factory.begin() as s:
        capture(s, 2027, "0.90")
    with pytest.raises(ValidationError, match="Correct it instead"), session_factory.begin() as s:
        capture(s, 2027, "0.91")
    with pytest.raises(IntegrityError), session_factory.begin() as s:
        s.add(
            RuleCapture(
                jurisdiction="au", key=RATE_KEY, fy=2027, value_json={"ccy": "AUD", "minor": 1},
                retrieved_on=TODAY, method="typed", captured_at=rulepack_store._now(),  # noqa: SLF001
                captured_kind="owner", captured_by="login:1",
            )
        )  # fmt: skip
    with (
        session_factory.begin() as s
    ):  # a different year, and a different key, are different figures
        capture(s, 2028, "0.90")
        capture(s, 2027, "5000", LIMIT_KEY)


def test_the_table_is_append_only_in_the_database(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as s:
        row_id = capture(s, 2027, "0.90").id
    for statement in (
        "DELETE FROM rule_capture",
        "UPDATE rule_capture SET value_json = '{}'",
        "UPDATE rule_capture SET quote = 'tampered'",
        "UPDATE rule_capture SET fy = 2030",
    ):
        with pytest.raises(DatabaseError, match="append-only"), session_factory.begin() as s:
            s.execute(text(statement))
    with session_factory.begin() as s:
        rulepack_store.withdraw_figure(s, CTX, row_id)
    for statement in (  # an ended row cannot be ended again, relinked, or revived
        "UPDATE rule_capture SET ended_at = NULL, ended_kind = NULL, ended_by = NULL",
        "UPDATE rule_capture SET superseded_by_id = 1",
        "UPDATE rule_capture SET ended_kind = 'superseded'",
    ):
        with pytest.raises(DatabaseError), session_factory.begin() as s:
            s.execute(text(statement))


def test_every_write_is_audited(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as s:
        row = capture(s, 2027, "0.90")
        new = rulepack_store.correct_figure(s, CTX, row.id, typed("0.91"), today=TODAY)
        rulepack_store.withdraw_figure(s, CTX, new.id)
    with session_factory() as s:
        events = s.scalars(
            select(AuditEvent)
            .where(AuditEvent.entity_type == "rule_capture")
            .order_by(AuditEvent.id)
        ).all()
    assert [e.action for e in events] == ["create", "supersede", "delete"]
    assert "0.90" in events[0].summary and "FY 2026-27" in events[0].summary
    assert events[1].before["value"] == {"ccy": "AUD", "minor": 90}  # type: ignore[index]
    assert events[1].after["value"] == {"ccy": "AUD", "minor": 91}  # type: ignore[index]


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"fy": 2027, "value": "abc"}, "value"),
        ({"fy": 2027, "value": "9999.00"}, "value"),  # beyond the declared plausible range
        ({"fy": 2027, "value": "5%"}, "value"),  # a percentage for an amount
        ({"fy": 2027, "value": "0.90", "page_url": "javascript:alert(1)"}, "page_url"),
        ({"fy": 2027, "value": "0.90", "retrieved_on": date(2030, 1, 1)}, "retrieved_on"),
        ({"fy": 2029, "value": "0.90"}, "fy"),  # further out than next year
        ({"fy": 2020, "value": "0.90"}, "fy"),  # before the pack's first year
        ({"fy": 2027, "value": "0.90", "key": "tax.nothing.here"}, "key"),
        ({"fy": 2027, "value": "0.90", "key": "sg_rate"}, "key"),  # a key with no spec
    ],
)
def test_a_figure_that_fails_the_validator_is_refused(
    session_factory: sessionmaker[Session], kwargs: dict[str, object], field: str
) -> None:
    key = str(kwargs.get("key", RATE_KEY))
    extra = {k: v for k, v in kwargs.items() if k not in ("fy", "value", "key")}
    with pytest.raises(ValidationError) as caught, session_factory.begin() as s:
        capture(s, int(kwargs["fy"]), str(kwargs["value"]), key, **extra)  # type: ignore[call-overload]
    assert [e.field for e in caught.value.errors] == [field]
    with session_factory() as s:
        assert s.scalar(select(RuleCapture.id)) is None


def test_a_large_change_needs_an_explicit_confirmation(
    session_factory: sessionmaker[Session],
) -> None:
    far = f"{SHIPPED.amount_minor * 3 / 100:.2f}"  # three times the previous year's figure
    with pytest.raises(ValidationError) as caught, session_factory.begin() as s:
        capture(s, 2027, far)
    (error,) = caught.value.errors
    assert error.field == "confirm_jump" and "Changed by more than" in error.message
    with session_factory.begin() as s:
        capture(s, 2027, far, confirm_jump=True)
    with session_factory() as s:
        assert effective(s).years[2027][RATE_KEY] == Money(SHIPPED.amount_minor * 3, "AUD")


def test_a_provisional_note_marks_the_figure_and_a_blank_one_does_not(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as s:
        capture(s, 2027, "0.90", provisional_note="  Announced, not yet law.  ")
        capture(s, 2028, "0.90", provisional_note="   ")
    with session_factory() as s:
        pack = effective(s)
    assert pack.provisional_note(2027, RATE_KEY) == "Announced, not yet law."
    assert pack.provisional_note(2028, RATE_KEY) is None


def test_withdrawing_or_correcting_an_ended_or_unknown_capture_is_refused(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as s:
        row_id = capture(s, 2027, "0.90").id
        rulepack_store.withdraw_figure(s, CTX, row_id)
    with pytest.raises(ValidationError), session_factory.begin() as s:
        rulepack_store.withdraw_figure(s, CTX, row_id)
    with pytest.raises(ValidationError), session_factory.begin() as s:
        rulepack_store.correct_figure(s, CTX, row_id, typed("0.91"), today=TODAY)
    with pytest.raises(NotFoundError), session_factory.begin() as s:
        rulepack_store.withdraw_figure(s, CTX, 9999)


def test_a_capture_that_no_longer_fits_is_ignored_listed_and_never_fatal(
    session_factory: sessionmaker[Session],
) -> None:
    # A release changed the key's range (or removed it): the stored row is now invalid. The merge
    # must not raise, the shipped figures apply, and the store lists the row as "no longer fits".
    with session_factory.begin() as s:
        good = capture(s, 2027, "0.90").id
        s.add(
            RuleCapture(
                jurisdiction="au", key=LIMIT_KEY, fy=2027, value_json={"nonsense": True},
                retrieved_on=TODAY, method="typed", captured_at=rulepack_store._now(),  # noqa: SLF001
                captured_kind="owner", captured_by="login:1",
            )
        )  # fmt: skip
        s.add(
            RuleCapture(
                jurisdiction="au", key="tax.removed.in_a_release", fy=2027,
                value_json={"ccy": "AUD", "minor": 5}, retrieved_on=TODAY, method="typed",
                captured_at=rulepack_store._now(), captured_kind="owner", captured_by="login:1",  # noqa: SLF001
            )
        )  # fmt: skip
    with session_factory() as s:
        pack = effective(s)
        problems = rulepack_store.unfit(s, "au")
    assert pack.years[2027][RATE_KEY] == Money(90, "AUD")  # the good one still applies
    assert LIMIT_KEY not in pack.years[2027]
    assert [c.key for c, _ in problems] == [LIMIT_KEY, "tax.removed.in_a_release"]
    assert all(reason for _, reason in problems) and good not in {c.id for c, _ in problems}
    assert pack.version_label.startswith("au/2026-27/r9+1:")  # only the one that applied counts


def test_the_year_listing_says_who_supplies_each_figure(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as s:
        capture(s, 2027, "0.90")
    with session_factory() as s:
        rows = {r.spec.key: r for r in rulepack_store.figures_for_year(s, "au", 2027)}
        older = {r.spec.key: r for r in rulepack_store.figures_for_year(s, "au", 2026)}
    rate = rows[RATE_KEY]
    assert (rate.state, rate.value, rate.shipped_value) == ("captured", "0.90", None)
    assert rate.previous == (2026, rulepack_figure_text(SHIPPED))
    assert rate.capture is not None and rate.capture.quote == "An invented sentence."
    assert rows[LIMIT_KEY].state == "missing" and rows[LIMIT_KEY].capture is None
    assert older[RATE_KEY].state == "shipped" and older[RATE_KEY].capture is None
    assert rows[RATE_KEY].range_text and rows[RATE_KEY].entry_hint


def rulepack_figure_text(value: Money) -> str:
    from finance360.core.money import format_amount

    return format_amount(value)


# --- a saved run keeps the capture id (ADR-0022 section 3) ---------------------------------

#: A projection reads the non-``tax.`` keys, so this run test declares ``sg_rate`` capturable on a
#: stand-in pack. The shipped pack declares no such spec; the real keys go through the same path.
SG_SPEC = FigureSpec(
    "sg_rate", RATE, CPI, "SG rate", PACK.param("sg_rate").source, "https://example.invalid/sg",
    "Invented box.", 0, 200_000,
)  # fmt: skip


@pytest.fixture
def sg_capturable() -> Iterator[None]:
    rulepack.unregister("au")
    rulepack.register(dataclasses.replace(PACK, specs=(*PACK.specs, SG_SPEC)))
    yield
    rulepack.unregister("au")
    rulepack.register(PACK)


def test_a_saved_run_records_the_effective_label_and_its_capture_id_resolves_after_a_withdraw(
    sample_factory: sessionmaker[Session], sg_capturable: None
) -> None:
    from finance360.capabilities.household import service as household
    from finance360.capabilities.projection import runs, scenarios
    from finance360.capabilities.projection.seed import seed_assumptions
    from finance360.core.models.scenario import Scenario

    def seed(s: Session) -> int:
        members = household.list_members(s)
        seed_assumptions(s)
        scenarios.set_assumptions(
            s,
            CTX,
            None,
            [
                scenarios.Change("annual_saving", "household", "", "0"),
                *(
                    scenarios.Change("retirement_age", "member", str(m.id), str(60 + i))
                    for i, m in enumerate(members)
                ),
            ],
        )
        scenarios.set_spending_phases(s, CTX, None, [("last_retirement", 0, "70000")])
        return s.scalars(select(Scenario).where(Scenario.key == "likely")).one().id

    with sample_factory.begin() as s:
        likely_id = seed(s)
        plain_id = runs.save_run(s, CTX, likely_id).id
        figure = rulepack_store.capture_figure(
            s, CTX, "au", "sg_rate", 2030, typed("12.5%"), today=date(2029, 8, 1)
        )
        capture_id = figure.id
        captured_id = runs.save_run(s, CTX, likely_id).id
    with sample_factory() as s:
        plain, captured = runs.get_run(s, plain_id), runs.get_run(s, captured_id)
    assert plain.header.rule_pack_version == "au/2026-27/r9"
    assert plain.run.prepared.rules.captured == ()  # type: ignore[union-attr]
    label = captured.header.rule_pack_version or ""
    assert label.startswith("au/2029-30/r9+1:")
    (used,) = captured.run.prepared.rules.captured  # type: ignore[union-attr]
    assert (used.key, used.fy, used.capture_id) == ("sg_rate", 2030, capture_id)
    with sample_factory.begin() as s:
        rulepack_store.withdraw_figure(s, CTX, capture_id)
    with sample_factory() as s:
        after = runs.get_run(s, captured_id)
        row = s.get(RuleCapture, capture_id)
        assert after.header.rule_pack_version == label  # the saved run is unchanged by the withdraw
        (still,) = after.run.prepared.rules.captured  # type: ignore[union-attr]
        assert still.capture_id == capture_id
        assert row is not None and row.quote == "An invented sentence."  # and it still resolves
        assert row.ended_kind == "withdrawn"
        assert effective(s).version_label == "au/2026-27/r9"
