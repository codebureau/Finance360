"""Position's Question and its inputs (ADR-0032 sections 1 and 8).

``own-and-owe`` is answered by the Position screen. Its three inputs are owned here because Position
owns the data: ``position.accounts`` (any open Account, else Not started), ``position.figures``
(Accounts with no usable figure today, ``missing``) and ``position.freshness`` (a stale Valuation,
from the three Valuation Due rules, weighted by value). The projection Question shares the last two.
An Account with no figure today has no value to weigh: a Valuation cannot be dated in the future,
and an Account in another currency with no rate has no AUD value, so its ``affected`` is unknown
and it makes the answer at most Partly ready. Everything is read-only and values are exact Money in
the Base currency; a share is judged in ``core.questions``, never here.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from sqlalchemy.orm import Session

from finance360.capabilities.position import derived, due, service
from finance360.capabilities.position.calculation import (
    AccountLine,
    Position,
    compute_position,
)
from finance360.core.due import IMPORTANT, DueItem, EffortLevel
from finance360.core.money import BASE_CURRENCY, Money
from finance360.core.questions import (
    MISSING,
    STALE,
    InputResult,
    Question,
    QuestionInput,
    Reason,
)

ACCOUNTS = "position.accounts"
FIGURES = "position.figures"
FRESHNESS = "position.freshness"

OWN_AND_OWE = "own-and-owe"

ZERO = Money.zero(BASE_CURRENCY)


def _lines(position: Position) -> list[AccountLine]:
    return [a for c in position.classes for n in c.categories for a in n.accounts]


def _loaded(session: Session, today: date) -> tuple[Position, service.Inputs]:
    """The Position today, and the inputs it was computed from (Accounts, FX rates)."""
    i = service.load_inputs(session)
    position = compute_position(
        on=today,
        accounts=derived.with_derived(i.accounts, i.derivers, today),
        categories=i.categories,
        member_ids=i.member_ids,
        fx=i.fx,
    )
    return position, i


def _gross(lines: Sequence[AccountLine]) -> Money:
    """The value the answer rests on: every valued line, assets and liabilities alike, in AUD."""
    total = ZERO
    for line in lines:
        if line.value is not None:
            total += Money(abs(line.value.amount_minor), BASE_CURRENCY)
    return total


def _accounts_check(
    session: Session, today: date, level: EffortLevel, items: Sequence[DueItem]
) -> InputResult:
    position, _ = _loaded(session, today)
    if _lines(position):
        return InputResult()
    return InputResult(
        empty=True,
        reasons=(
            Reason(
                kind=MISSING,
                subject_key="accounts",
                title="No Accounts have been added yet",
                effect="Accounts are what your net position is built from.",
                affected=None,
                link="/accounts",
            ),
        ),
    )


def _figures_check(
    session: Session, today: date, level: EffortLevel, items: Sequence[DueItem]
) -> InputResult:
    position, _ = _loaded(session, today)
    lines = _lines(position)
    if not lines:
        return InputResult(empty=True)
    reasons: list[Reason] = []
    for line in lines:
        if line.value is not None:
            continue
        reasons.append(
            Reason(
                kind=MISSING,
                subject_key=f"account:{line.account_id}",
                title=f"Your Position leaves out {line.name}: it has no figure to count today",
                effect="A balance or a rate puts it in your net position.",
                affected=None,
                link=f"/accounts/{line.account_id}",
            )
        )
    return InputResult(total=_gross(lines), reasons=tuple(reasons))


def _account_id(subject_key: str) -> int | None:
    """The Account of a Valuation Due item (``account:12@2026-03-31``); ``None`` for an Account
    with no figure yet (``@none``), which ``position.figures`` reports instead."""
    head, _, period = subject_key.partition("@")
    kind, _, ident = head.partition(":")
    if kind != "account" or period == "none" or not ident.isdigit():
        return None
    return int(ident)


def _freshness_check(
    session: Session, today: date, level: EffortLevel, items: Sequence[DueItem]
) -> InputResult:
    position, _ = _loaded(session, today)
    lines = _lines(position)
    if not lines:
        return InputResult(empty=True)
    value = {
        line.account_id: Money(abs(line.value.amount_minor), BASE_CURRENCY)
        for line in lines
        if line.value is not None
    }
    reasons: list[Reason] = []
    for item in items:
        account_id = _account_id(item.subject_key)
        if account_id is None:
            continue
        reasons.append(
            Reason(
                kind=STALE,
                subject_key=item.subject_key,
                title=item.title,
                effect=item.effect,
                affected=value.get(account_id),
                link=item.link,
            )
        )
    return InputResult(total=_gross(lines), reasons=tuple(reasons))


INPUTS: tuple[QuestionInput, ...] = (
    QuestionInput(ACCOUNTS, "Accounts", IMPORTANT, (), _accounts_check),
    QuestionInput(FIGURES, "Account figures", IMPORTANT, (), _figures_check),
    QuestionInput(
        FRESHNESS,
        "Fresh balances",
        IMPORTANT,
        (due.MANUAL_VALUATION, due.SUPER_VALUATION, due.SLOW_VALUATION),
        _freshness_check,
    ),
)

QUESTIONS: tuple[Question, ...] = (
    Question(
        key=OWN_AND_OWE,
        wording="What do we own and owe?",
        screen="/position",
        area="Where we stand",
        inputs=(ACCOUNTS, FIGURES, FRESHNESS),
        ask_prompt="What do we own and owe, and what does the Position need to be right?",
    ),
)
