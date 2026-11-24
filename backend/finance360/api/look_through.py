"""The Account look-through endpoint. Thin: every rule lives in ``capabilities.lookthrough``.

Amounts are Money (exact decimal strings with their currency); percentages are display only,
rounded once from Money.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from finance360.api.household import ERRORS, ContextDep, SessionDep
from finance360.api.transactions import MoneyOut, money_out
from finance360.capabilities.lookthrough import option_links, service
from finance360.capabilities.lookthrough.lines import Composition
from finance360.core.exposure import ASSET_CLASS, percent_string
from finance360.core.models.instrument import Instrument

router = APIRouter(prefix="/api", tags=["look-through"])


class ContributionOut(BaseModel):
    path: list[str] = Field(
        description="Instrument names from the held fund down to the one that gave."
    )
    amount: MoneyOut


class LookThroughLineOut(BaseModel):
    kind: Literal["label", "no_profile", "not_classified", "unallocated"]
    key: str | None = Field(description="The Exposure label key of a label line.")
    name: str
    amount: MoneyOut
    percent: str | None = Field(
        description="Of the Account value, two decimal places; display only."
    )
    instrument_id: int | None = Field(description="The Instrument a No profile line asks for.")
    through_fund: bool = Field(
        description="An underlying fund of a fund-of-funds gave to this line (see contributions)."
    )
    contributions: list[ContributionOut] = Field(
        description="Each route through the funds that gave to this line, summing to its amount."
    )
    reason: Literal["cycle", "too_deep"] | None = Field(
        description="A No profile line behind a loop of funds, or nested beyond the cap."
    )
    option_name: str | None = Field(
        description="A No profile line for an Investment option not mapped to a fund yet."
    )


class TrimmedOut(BaseModel):
    label_key: str
    percent: str


class UnderlyingOut(BaseModel):
    path: list[str] = Field(description="Instrument names from the held fund down to this one.")
    amount: MoneyOut
    profile_as_at: date
    dated_after: bool
    shortfall_percent: str
    trimmed: list[TrimmedOut]
    omitted: bool


class LookThroughPartOut(BaseModel):
    instrument_id: int | None
    name: str
    amount: MoneyOut
    profile_as_at: date | None
    dated_after: bool = Field(description="The profile used is dated after the as-at date.")
    shortfall_percent: str = Field(description="A within-tolerance shortfall, in Not classified.")
    trimmed: list[TrimmedOut] = Field(description="Trimmed from the largest weight (over 100).")
    omitted: bool = Field(
        description="The profile gives this Dimension no weights, so all of it is Not classified."
    )
    underlying: list[UnderlyingOut] = Field(
        description="The underlying funds a fund-of-funds Holding resolved through."
    )
    option_name: str | None = Field(
        description="An Investment option part: its name on the Super statement."
    )
    instrument_name: str | None = Field(
        description="The Instrument an Investment option is mapped to; null while unmapped."
    )


class LookThroughOut(BaseModel):
    applies: bool = Field(
        description="False when the Account has no look-through (not Holdings, not a super fund)."
    )
    source: Literal["holdings", "super_statement"] | None = Field(
        description="Where the parts come from: the Holdings, or the Super statement's options."
    )
    statement_id: int | None = Field(description="The Super statement the parts come from.")
    statement_date: date | None = Field(description="That Super statement's date (30 June).")
    change_since: date | None = Field(
        description="Set when the Valuation is dated after the Super statement: Unallocated is "
        "then the change since this date, judged against the stale tolerance."
    )
    as_at: date
    status: Literal["ok", "not_analysed"]
    reason: str | None
    dimension_key: str
    dimension_name: str
    total: MoneyOut | None = Field(description="The Account's value in the Position.")
    lines: list[LookThroughLineOut]
    parts: list[LookThroughPartOut]
    flagged: bool = Field(description="Unallocated is beyond the reconcile tolerance.")
    tolerance: MoneyOut | None


def _lines(c: Composition) -> list[LookThroughLineOut]:
    return [
        LookThroughLineOut(
            kind=line.kind,
            key=line.key,
            name=line.name,
            amount=money_out(line.amount),  # type: ignore[arg-type]
            percent=line.percent,
            instrument_id=line.instrument_id,
            through_fund=bool(line.contributions),
            contributions=[
                ContributionOut(
                    path=list(x.path),
                    amount=money_out(x.amount),  # type: ignore[arg-type]
                )
                for x in line.contributions
            ],
            reason=line.reason,
            option_name=line.option_name,
        )
        for line in c.lines
    ]


def _parts(c: Composition) -> list[LookThroughPartOut]:
    return [
        LookThroughPartOut(
            instrument_id=p.instrument_id,
            name=p.name,
            amount=money_out(p.amount),  # type: ignore[arg-type]
            profile_as_at=p.profile_as_at,
            dated_after=p.dated_after,
            shortfall_percent=percent_string(p.shortfall_ppm),
            trimmed=[TrimmedOut(label_key=k, percent=percent_string(w)) for k, w in p.trimmed],
            omitted=p.omitted,
            underlying=[
                UnderlyingOut(
                    path=list(u.path),
                    amount=money_out(u.amount),  # type: ignore[arg-type]
                    profile_as_at=u.profile_as_at,
                    dated_after=u.dated_after,
                    shortfall_percent=percent_string(u.shortfall_ppm),
                    trimmed=[
                        TrimmedOut(label_key=k, percent=percent_string(w)) for k, w in u.trimmed
                    ],
                    omitted=u.omitted,
                )
                for u in p.underlying
            ],
            option_name=p.option,
            instrument_name=p.instrument_name,
        )
        for p in c.parts
    ]


@router.get(
    "/accounts/{account_id}/look-through",
    response_model=LookThroughOut,
    operation_id="getLookThrough",
    responses=ERRORS,  # type: ignore[arg-type]
)
def get_look_through(
    account_id: int,
    request: Request,
    session: SessionDep,
    on: date | None = None,
    dimension: str = ASSET_CLASS,
) -> LookThroughOut:
    """An Account's value on a date split by an Exposure Dimension (asset class, region, sector or
    exposure currency), from its Holdings' (or, for a super fund, its Super statement's Investment
    options') manually entered Exposure profiles. The lines, including No profile, Not
    classified and Unallocated, sum exactly to the Account's value in the Position. ``on``
    defaults to the latest date the Account's Streams' Coverage reaches (at most today), as the
    Derived value does; ``applies`` is false when the Account is neither built from Holdings nor
    a super fund."""
    settings = request.app.state.settings
    found = service.account_look_through(
        session, account_id, on, dimension, settings.price_stale_days
    )
    c = found.composition
    return LookThroughOut(
        applies=found.applies,
        source=found.source,
        statement_id=found.statement_id,
        statement_date=found.statement_date,
        change_since=c.change_since if c else None,
        as_at=found.on,
        status=found.status,
        reason=found.reason,
        dimension_key=found.dimension_key,
        dimension_name=found.dimension_name,
        total=money_out(c.total) if c else None,
        lines=_lines(c) if c else [],
        parts=_parts(c) if c else [],
        flagged=c.flagged if c else False,
        tolerance=money_out(c.tolerance) if c else None,
    )


class OptionLinkIn(BaseModel):
    option_name: str = Field(default="", description="The Investment option's name, as stated.")
    instrument_id: int | None = Field(
        default=None, description="An existing Instrument to map the option to."
    )
    create_instrument: bool = Field(
        default=False,
        description="Create a fund Instrument from the option's name instead (Account currency).",
    )


class OptionLinkOut(BaseModel):
    option_name: str
    instrument_id: int
    instrument_name: str


@router.put(
    "/accounts/{account_id}/investment-option-links",
    response_model=OptionLinkOut,
    operation_id="mapInvestmentOption",
    responses=ERRORS,  # type: ignore[arg-type]
)
def map_investment_option(
    account_id: int, body: OptionLinkIn, session: SessionDep, ctx: ContextDep
) -> OptionLinkOut:
    """Map a super Account's Investment option to an Instrument, or create a fund from its name.
    Later Super statements with the same name (any case or spacing) need no new mapping."""
    link = option_links.map_option(
        session,
        ctx,
        account_id,
        body.option_name,
        instrument_id=body.instrument_id,
        create_instrument=body.create_instrument,
    )
    instrument = session.get_one(Instrument, link.instrument_id)
    return OptionLinkOut(
        option_name=link.option_name, instrument_id=instrument.id, instrument_name=instrument.name
    )
