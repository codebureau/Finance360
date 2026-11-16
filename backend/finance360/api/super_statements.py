"""Super statement endpoints. Thin: every rule lives in ``capabilities.position.super_statements``.

Amounts cross the API as plain decimal strings in the Account's currency (``"1234.56"``); an amount
the statement did not state is ``null`` (which is not zero). Never a JSON float.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from finance360.api.household import ERRORS, ContextDep, MessageBody, SessionDep
from finance360.capabilities.position import super_statements as service
from finance360.core.models.super_statement import AMOUNTS, UNITS_SCALE, SuperStatement
from finance360.core.money import Money, format_amount
from finance360.core.price import PRICE_SCALE
from finance360.core.rulepack import fy_of

router = APIRouter(prefix="/api", tags=["super-statements"])

#: A plain decimal string in the Account's currency, or null for "not stated".
_AMOUNT = "Decimal string in the Account's currency, e.g. 1234.56."
_OPTIONAL = "Decimal string, or null/blank when the statement does not state it (not zero)."


class OptionLineIn(BaseModel):
    name: str = ""
    value: str = Field(default="", description=_AMOUNT)
    units: str | None = Field(default=None, description="At most 6 decimal places; not negative.")
    unit_price: str | None = Field(
        default=None, description="At most 6 decimal places; not negative."
    )


class SuperStatementIn(BaseModel):
    period_start: date = Field(description="1 July.")
    period_end: date = Field(description="30 June of the next year.")
    opening: str = Field(default="", description=_AMOUNT)
    contributions_in: str = Field(default="", description=_AMOUNT)
    investment_returns: str = Field(default="", description=_AMOUNT)
    withdrawals: str = Field(default="", description=_AMOUNT)
    closing: str = Field(default="", description=_AMOUNT)
    contributions_tax: str | None = Field(default=None, description=_OPTIONAL)
    fees_before_rebate: str | None = Field(default=None, description=_OPTIONAL)
    rebate: str | None = Field(default=None, description=_OPTIONAL)
    fees_inside_investment: str | None = Field(default=None, description=_OPTIONAL)
    division_293_released: str | None = Field(default=None, description=_OPTIONAL)
    other_withdrawals: str | None = Field(default=None, description=_OPTIONAL)
    insurance_premiums: str | None = Field(default=None, description=_OPTIONAL)
    concessional_received: str | None = Field(default=None, description=_OPTIONAL)
    non_concessional_received: str | None = Field(default=None, description=_OPTIONAL)
    options: list[OptionLineIn] = []
    ticked_checks: list[str] = Field(
        default=[], description="The names of the checks the owner has ticked as checked."
    )


class SuperCheckOut(BaseModel):
    name: Literal["roll", "outflows", "options"]
    status: Literal["ok", "warn", "not_applicable"]
    message: str


class OptionLineOut(BaseModel):
    name: str
    units: str | None
    unit_price: str | None
    value: str


class SuperStatementOut(BaseModel):
    id: int
    account_id: int
    financial_year: int = Field(description="The year the financial year ends in, e.g. 2026.")
    period_start: date
    period_end: date
    currency: str
    opening: str
    contributions_in: str
    investment_returns: str
    withdrawals: str
    closing: str
    contributions_tax: str | None
    fees_before_rebate: str | None
    rebate: str | None
    fees_inside_investment: str | None
    division_293_released: str | None
    other_withdrawals: str | None
    insurance_premiums: str | None
    concessional_received: str | None
    non_concessional_received: str | None
    options: list[OptionLineOut]
    valuation_id: int = Field(description="The 30 June Valuation this statement recorded.")
    ticked_checks: list[str]
    checks: list[SuperCheckOut] = Field(
        description="The checks as they stand on the saved figures."
    )
    recorded_at: datetime = Field(description="UTC.")


def _input(body: SuperStatementIn) -> service.SuperStatementInput:
    return service.SuperStatementInput(
        period_start=body.period_start,
        period_end=body.period_end,
        opening=body.opening,
        contributions_in=body.contributions_in,
        investment_returns=body.investment_returns,
        withdrawals=body.withdrawals,
        closing=body.closing,
        contributions_tax=body.contributions_tax,
        fees_before_rebate=body.fees_before_rebate,
        rebate=body.rebate,
        fees_inside_investment=body.fees_inside_investment,
        division_293_released=body.division_293_released,
        other_withdrawals=body.other_withdrawals,
        insurance_premiums=body.insurance_premiums,
        concessional_received=body.concessional_received,
        non_concessional_received=body.non_concessional_received,
        options=[
            service.OptionLineInput(o.name, o.value, o.units, o.unit_price) for o in body.options
        ],
        ticked_checks=body.ticked_checks,
    )


def _checks_out(results: list[service.CheckResult]) -> list[SuperCheckOut]:
    return [SuperCheckOut(name=r.name, status=r.status, message=r.message) for r in results]  # type: ignore[arg-type]


def _out(statement: SuperStatement) -> SuperStatementOut:
    def text(name: str) -> str | None:
        value = statement.amount(name)
        return None if value is None else format_amount(value)

    amounts = {name: text(name) for name in AMOUNTS}
    results = service.evaluate_checks(
        {name: getattr(statement, f"{name}_minor") for name in AMOUNTS},
        [o.value_minor for o in statement.options],
        statement.currency,
    )
    return SuperStatementOut(
        id=statement.id,
        account_id=statement.account_id,
        financial_year=fy_of(statement.period_end),
        period_start=statement.period_start,
        period_end=statement.period_end,
        currency=statement.currency,
        **amounts,  # type: ignore[arg-type]
        options=[
            OptionLineOut(
                name=o.name,
                units=None if o.units is None else format(o.units, f".{UNITS_SCALE}f"),
                unit_price=None
                if o.unit_price is None
                else format(o.unit_price, f".{PRICE_SCALE}f"),
                value=format_amount(Money(o.value_minor, statement.currency)),
            )
            for o in statement.options
        ],
        valuation_id=statement.valuation_id,
        ticked_checks=service.ticked_checks(statement),
        checks=_checks_out(results),
        recorded_at=statement.recorded_at,
    )


@router.get(
    "/accounts/{account_id}/super-statements",
    response_model=list[SuperStatementOut],
    operation_id="listSuperStatements",
    responses={404: {"model": MessageBody}},  # type: ignore[arg-type]
)
def list_super_statements(account_id: int, session: SessionDep) -> list[SuperStatementOut]:
    """The Account's current Super statements, newest financial year first."""
    return [_out(s) for s in service.list_super_statements(session, account_id)]


@router.get(
    "/super-statements/{statement_id}",
    response_model=SuperStatementOut,
    operation_id="getSuperStatement",
    responses={404: {"model": MessageBody}},  # type: ignore[arg-type]
)
def get_super_statement(statement_id: int, session: SessionDep) -> SuperStatementOut:
    """A Super statement as it stands now: an id replaced by a correction returns its successor
    (so a saved address keeps working); a removed statement is 404."""
    return _out(service.current_super_statement(session, statement_id))


@router.post(
    "/accounts/{account_id}/super-statements/checks",
    response_model=list[SuperCheckOut],
    operation_id="checkSuperStatement",
    responses=ERRORS,  # type: ignore[arg-type]
)
def check_super_statement(
    account_id: int, body: SuperStatementIn, session: SessionDep
) -> list[SuperCheckOut]:
    """Run the checks on a draft so the form can warn as figures are typed. Nothing is stored."""
    return _checks_out(service.check_super_statement(session, account_id, _input(body)))


@router.post(
    "/accounts/{account_id}/super-statements",
    status_code=201,
    response_model=SuperStatementOut,
    operation_id="addSuperStatement",
    responses=ERRORS,  # type: ignore[arg-type]
)
def add_super_statement(
    account_id: int, body: SuperStatementIn, session: SessionDep, ctx: ContextDep
) -> SuperStatementOut:
    """Record a Super statement and its 30 June Valuation. A failed check needs its tick."""
    return _out(service.create_super_statement(session, ctx, account_id, _input(body)))


@router.put(
    "/super-statements/{statement_id}",
    response_model=SuperStatementOut,
    operation_id="correctSuperStatement",
    responses=ERRORS,  # type: ignore[arg-type]
)
def correct_super_statement(
    statement_id: int, body: SuperStatementIn, session: SessionDep, ctx: ContextDep
) -> SuperStatementOut:
    """Correct a current statement: returns the new one; the old one stays, superseded."""
    return _out(service.correct_super_statement(session, ctx, statement_id, _input(body)))


@router.delete(
    "/super-statements/{statement_id}",
    status_code=204,
    operation_id="removeSuperStatement",
    responses=ERRORS,  # type: ignore[arg-type]
)
def remove_super_statement(statement_id: int, session: SessionDep, ctx: ContextDep) -> Response:
    """Remove a current statement and its Valuation; both stay in the history, superseded."""
    service.remove_super_statement(session, ctx, statement_id)
    return Response(status_code=204)
