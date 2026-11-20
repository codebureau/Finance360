"""Lodged result and return worksheet endpoints (ADR-0019 section 8). Thin: the rules live in
``capabilities.tax.lodged`` and ``capabilities.tax.compare``.

Amounts cross the API as plain AUD decimal strings (``"1234.56"``), never a JSON number. A Lodged
result is entered once per Member and year; an amended notice is a ``PUT`` that supersedes it and
keeps the earlier one, which stays readable (``GET`` by id, and ``history`` on the worksheet).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field, StrictInt

from finance360.api.household import ERRORS, ContextDep, SessionDep
from finance360.capabilities.tax import compare, lodged
from finance360.core.money import Money, format_amount
from finance360.core.rulepack import fy_label

router = APIRouter(prefix="/api/tax/lodged-results", tags=["tax"])

_AMOUNT = "AUD decimal string, as printed on the notice, e.g. 1234.56."


class LodgedOtherIn(BaseModel):
    name: str = Field(default="", description="The line's name as the notice gives it.")
    amount: str = Field(default="", description=_AMOUNT)


class LodgedResultIn(BaseModel):
    member_id: StrictInt
    financial_year: StrictInt = Field(description="The year it ends in: 2026 is FY 2025-26.")
    assessed_on: date | None = Field(default=None, description="The date on the notice.")
    items: dict[str, str] = Field(
        default_factory=lambda: {},
        description="Result key to amount. Keys: taxable_income, tax_on_taxable_income, offsets, "
        "medicare_levy, medicare_levy_surcharge, help_repayment, total_tax, credits, net_outcome "
        "(a refund is positive, an amount payable negative). A blank amount is left out.",
    )
    other: list[LodgedOtherIn] = Field(
        default_factory=lambda: [],
        description="Named lines the engine has no key for.",
    )


class LodgedItemOut(BaseModel):
    key: str
    amount: str


class LodgedOtherOut(BaseModel):
    name: str
    amount: str


class LodgedResultOut(BaseModel):
    id: int
    member_id: int
    financial_year: int
    financial_year_label: str
    assessed_on: date
    items: list[LodgedItemOut]
    other: list[LodgedOtherOut]
    recorded_at: datetime
    current: bool = Field(description="False: an amended notice has replaced it (it is kept).")
    superseded_by_id: int | None


def _amount(money: Money | None) -> str:
    return format_amount(money) if money is not None else ""


def _out(view: lodged.LodgedView) -> LodgedResultOut:
    return LodgedResultOut(
        id=view.id,
        member_id=view.member_id,
        financial_year=view.financial_year,
        financial_year_label=fy_label(view.financial_year),
        assessed_on=view.assessed_on,
        items=[LodgedItemOut(key=k, amount=_amount(a)) for k, a in view.result.items],
        other=[LodgedOtherOut(name=o.name, amount=_amount(o.amount)) for o in view.result.other],
        recorded_at=view.recorded_at,
        current=view.current,
        superseded_by_id=view.superseded_by_id,
    )


def _input(body: LodgedResultIn) -> lodged.LodgedInput:
    return lodged.LodgedInput(
        member_id=body.member_id,
        financial_year=body.financial_year,
        assessed_on=body.assessed_on,
        items=body.items,
        other=[lodged.OtherInput(o.name, o.amount) for o in body.other],
    )


class WorksheetLineOut(BaseModel):
    key: str = Field(description="A result key.")
    status: Literal["matched", "rounding", "differs", "not_lodged", "no_estimate"] = Field(
        description="matched: equal to the cent. rounding: net outcome off by no more than the "
        "notice's rounding (5 cents); the difference is still shown. differs. not_lodged: the "
        "notice has no amount for it. no_estimate: the estimate could not be calculated."
    )
    engine: str | None = Field(description="The estimate's amount.")
    lodged: str | None = Field(description="The notice's amount.")
    difference: str | None = Field(description="The estimate less the notice; null if not both.")


class WorksheetUnmatchedOut(BaseModel):
    name: str = Field(description="The lodged line's name, or its key the engine does not model.")
    amount: str
    is_other: bool = Field(description="True for a named other line on the notice.")


class WorksheetOut(BaseModel):
    member_id: int
    member_name: str
    financial_year: int
    financial_year_label: str
    dependants: int
    current: LodgedResultOut | None
    history: list[LodgedResultOut] = Field(
        description="Every notice for the Member and year, newest first, the current one first."
    )
    lines: list[WorksheetLineOut] = Field(description="Every result key, in the Estimate's order.")
    unmatched: list[WorksheetUnmatchedOut] = Field(
        description="Every lodged item the engine has no value to compare with."
    )
    matched_count: int
    differs_count: int
    incomplete: bool = Field(description="The estimate leaves out return items it does not model.")
    engine_version: int | None
    rule_pack_version: str | None
    problems: list[str] = Field(description="Why the estimate could not be calculated, if so.")


def _worksheet_lines(sheet: lodged.Worksheet) -> list[WorksheetLineOut]:
    by_key = (
        {line.key: line for line in sheet.comparison.lines if not line.is_other}
        if sheet.comparison
        else {}
    )
    notice = dict(sheet.current.result.items) if sheet.current else {}
    lines: list[WorksheetLineOut] = []
    for key in compare.RESULT_KEYS:
        engine: Money | None = getattr(sheet.estimate, key) if sheet.estimate else None
        found = by_key.get(key)
        if found is not None:
            status = found.status
        elif sheet.estimate is None:
            status = "no_estimate"
        else:
            status = "not_lodged"
        lines.append(
            WorksheetLineOut(
                key=key,
                status=status,  # type: ignore[arg-type]
                engine=_amount(engine) if engine is not None else None,
                lodged=_amount(notice[key]) if key in notice else None,
                difference=_amount(found.difference)
                if found is not None and found.difference is not None
                else None,
            )
        )
    return lines


def _unmatched(sheet: lodged.Worksheet) -> list[WorksheetUnmatchedOut]:
    """Every lodged item the engine has no value to compare with. Shown even with no estimate:
    the other lines are the notice's own, and the engine never had a key for them."""
    if sheet.current is None:
        return []
    return [
        WorksheetUnmatchedOut(name=o.name, amount=_amount(o.amount), is_other=True)
        for o in sheet.current.result.other
    ]


@router.get(
    "/worksheet",
    response_model=WorksheetOut,
    operation_id="getReturnWorksheet",
    responses=ERRORS,  # type: ignore[arg-type]
)
def get_worksheet(
    session: SessionDep,
    member_id: Annotated[int, Query(description="The Member.")],
    financial_year: Annotated[int, Query(description="The year it ends in: 2026 is FY 2025-26.")],
    dependants: Annotated[int, Query(description="Dependent children, for the estimate.")] = 0,
) -> WorksheetOut:
    """The estimate beside the current Lodged result: each difference, every lodged item the engine
    cannot match, and the earlier (superseded) notices."""
    sheet = lodged.worksheet(session, member_id, financial_year, dependants)
    lines = _worksheet_lines(sheet)
    return WorksheetOut(
        member_id=sheet.member_id,
        member_name=sheet.member_name,
        financial_year=sheet.financial_year,
        financial_year_label=fy_label(sheet.financial_year),
        dependants=sheet.dependants,
        current=_out(sheet.current) if sheet.current else None,
        history=[_out(v) for v in sheet.history],
        lines=lines,
        unmatched=_unmatched(sheet),
        matched_count=sum(1 for x in lines if x.status in ("matched", "rounding")),
        differs_count=sum(1 for x in lines if x.status == "differs"),
        incomplete=sheet.incomplete,
        engine_version=sheet.engine_version,
        rule_pack_version=sheet.rule_pack_version,
        problems=list(sheet.problems),
    )


@router.post(
    "",
    status_code=201,
    response_model=LodgedResultOut,
    operation_id="createLodgedResult",
    responses=ERRORS,  # type: ignore[arg-type]
)
def create_lodged_result(
    body: LodgedResultIn, session: SessionDep, ctx: ContextDep
) -> LodgedResultOut:
    """Enter a Member's notice for a year. Audited. A year that already has one is refused: enter
    the amended notice with PUT."""
    return _out(lodged.create_lodged_result(session, ctx, _input(body)))


@router.get(
    "/{lodged_id}",
    response_model=LodgedResultOut,
    operation_id="getLodgedResult",
    responses=ERRORS,  # type: ignore[arg-type]
)
def get_lodged_result(lodged_id: int, session: SessionDep) -> LodgedResultOut:
    """A Lodged result by id, current or superseded."""
    return _out(lodged.get_lodged_result(session, lodged_id))


@router.put(
    "/{lodged_id}",
    response_model=LodgedResultOut,
    operation_id="amendLodgedResult",
    responses=ERRORS,  # type: ignore[arg-type]
)
def amend_lodged_result(
    lodged_id: int, body: LodgedResultIn, session: SessionDep, ctx: ContextDep
) -> LodgedResultOut:
    """Enter an amended notice. The current one is superseded and kept; the new row is returned.
    Audited."""
    return _out(lodged.amend_lodged_result(session, ctx, lodged_id, _input(body)))
