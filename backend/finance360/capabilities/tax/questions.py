"""The Tax Capability's Question and its inputs (ADR-0032 sections 1 and 8).

``tax-this-year`` is answered by the Tax estimate. Its four inputs are owned here because tax owns
the data: ``tax.records`` (any Tax record, else Not started), ``tax.income_statement`` (the income
statement Due rule), ``tax.rule_figures`` (the Rule figure Due rules) and ``tax.modelled`` (a Return
label with a figure the engine does not read, ``incomplete``, value unknown).

Without a Tax record nothing of this kind has been given yet, so every input is Not started and the
Question is Not started. A reason here has no value to weigh (``affected`` is ``None``), so it makes
the answer at most Partly ready; only an estimate that cannot be built is ``blocking``. The tax
engine is untouched: it is read through the service, like the Tax screen does.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from finance360.capabilities.household import service as household
from finance360.capabilities.tax import due, labels, rule_figures_due, service
from finance360.capabilities.tax import input as tax_input
from finance360.core.due import IMPORTANT, DueItem, EffortLevel
from finance360.core.errors import ValidationError
from finance360.core.models.tax_record import TaxRecord
from finance360.core.questions import (
    INCOMPLETE,
    MISSING,
    STALE,
    InputResult,
    Question,
    QuestionInput,
    Reason,
    ReasonKind,
)
from finance360.core.rulepack import fy_label, fy_of

RECORDS = "tax.records"
INCOME_STATEMENT = "tax.income_statement"
RULE_FIGURES = "tax.rule_figures"
MODELLED = "tax.modelled"

TAX_THIS_YEAR = "tax-this-year"

#: How each Rule figure Due rule reads as a reason. "Now firm" is housekeeping (the release
#: already supplies the figure), so it never weighs on the answer and is not listed.
_RULE_FIGURE_KINDS: dict[str, ReasonKind] = {
    rule_figures_due.NOT_CAPTURED_RULE: MISSING,
    rule_figures_due.DIFFERS_RULE: STALE,
    rule_figures_due.NO_LONGER_FITS_RULE: INCOMPLETE,
}


def _has_records(session: Session) -> bool:
    return session.scalars(select(TaxRecord.id).limit(1)).first() is not None


def _records_check(
    session: Session, today: date, level: EffortLevel, items: Sequence[DueItem]
) -> InputResult:
    if _has_records(session):
        return InputResult()
    return InputResult(
        empty=True,
        reasons=(
            Reason(
                kind=MISSING,
                subject_key="records",
                title="No Tax records have been added yet",
                effect="Tax records are what the Tax estimate is built from.",
                affected=None,
                link="/tax/records",
            ),
        ),
    )


def _due_reasons(items: Sequence[DueItem], kinds: dict[str, ReasonKind]) -> tuple[Reason, ...]:
    return tuple(
        Reason(
            kind=kinds[item.rule_key],
            subject_key=item.subject_key,
            title=item.title,
            effect=item.effect,
            affected=None,
            link=item.link,
        )
        for item in items
        if item.rule_key in kinds
    )


def _income_statement_check(
    session: Session, today: date, level: EffortLevel, items: Sequence[DueItem]
) -> InputResult:
    if not _has_records(session):
        return InputResult(empty=True)
    return InputResult(reasons=_due_reasons(items, {due.INCOME_STATEMENT_RULE: MISSING}))


def _rule_figures_check(
    session: Session, today: date, level: EffortLevel, items: Sequence[DueItem]
) -> InputResult:
    if not _has_records(session):
        return InputResult(empty=True)
    return InputResult(reasons=_due_reasons(items, _RULE_FIGURE_KINDS))


def _modelled_check(
    session: Session, today: date, level: EffortLevel, items: Sequence[DueItem]
) -> InputResult:
    if not _has_records(session):
        return InputResult(empty=True)
    members = [m.id for m in household.list_members(session)][: tax_input.MAX_MEMBERS]
    fy = fy_of(today)
    try:
        estimate = service.estimate(session, financial_year=fy, dependants=0, member_ids=members)
    except ValidationError as problem:
        # The estimate cannot be built for this year (a figure the Rule pack lacks, a record it
        # cannot count): the answer cannot be computed. Say why, and link to where it is fixed.
        year = next((y for y in service.setup(session).years if y.financial_year == fy), None)
        pack_lacks = year is None or not year.complete
        return InputResult(
            blocking=True,
            reasons=(
                Reason(
                    kind=MISSING,
                    subject_key=f"fy{fy}",
                    title=f"Your Tax estimate for FY {fy_label(fy)} cannot be worked out yet",
                    effect=" ".join(e.message for e in problem.errors),
                    affected=None,
                    link=f"/rule-figures?fy={fy}&missing=1" if pack_lacks else "/tax",
                ),
            ),
        )
    reasons: list[Reason] = []
    seen: set[str] = set()
    for u in estimate.result.unmodelled:
        if u.label_key in seen:
            continue
        seen.add(u.label_key)
        label = labels.get(u.label_key)
        name = label.name if label is not None else u.label_key
        reasons.append(
            Reason(
                kind=INCOMPLETE,
                subject_key=f"label:{u.label_key}@fy{fy}",
                title=f"Your Tax estimate does not model {name}",
                effect="The estimate leaves it out, so the result is approximate.",
                affected=None,
                link="/tax",
            )
        )
    return InputResult(reasons=tuple(reasons))


INPUTS: tuple[QuestionInput, ...] = (
    QuestionInput(RECORDS, "Tax records", IMPORTANT, (), _records_check),
    QuestionInput(
        INCOME_STATEMENT,
        "Income statements",
        IMPORTANT,
        (due.INCOME_STATEMENT_RULE,),
        _income_statement_check,
    ),
    QuestionInput(
        RULE_FIGURES,
        "Rule figures",
        IMPORTANT,
        tuple(_RULE_FIGURE_KINDS),
        _rule_figures_check,
    ),
    QuestionInput(MODELLED, "Modelled labels", IMPORTANT, (), _modelled_check),
)

QUESTIONS: tuple[Question, ...] = (
    Question(
        key=TAX_THIS_YEAR,
        wording="How much tax will we pay, or get back?",
        screen="/tax",
        area="This tax year",
        inputs=(RECORDS, INCOME_STATEMENT, RULE_FIGURES, MODELLED),
        ask_prompt="How much tax will we pay, and what does the Tax estimate need?",
    ),
)
