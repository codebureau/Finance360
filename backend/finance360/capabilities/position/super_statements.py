"""Super statement services (ADR-0025).

A super fund's annual statement for one Account and one financial year: the balance roll, an
optional outflow breakdown, contributions received and Investment option lines. Saving writes a
``statement``-source Valuation at 30 June in the same session (the caller owns the transaction),
and the statement holds its id. A correction supersedes the statement and corrects the Valuation
with it; a removal supersedes both. Nothing is updated or deleted.

Three checks (roll, outflows, options) are pure functions over integer minor units. A failed check
does not block: the owner ticks that they have checked it, and the ticked names are stored. Only
malformed input blocks. Tax and projection read the statements through ``list_super_years``; this
module imports neither (ADR-0025 section 1).

The Valuation is written first so that a refused Valuation (a future date, a closed Account) leaves
nothing behind; the statement rows follow and have already been validated.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from finance360.capabilities.position import valuations
from finance360.core import audit
from finance360.core.audit import AuditContext
from finance360.core.errors import FieldError, NotFoundError, ValidationError
from finance360.core.models.account import Account
from finance360.core.models.super_statement import (
    AMOUNTS,
    NAME_MAX,
    OPTIONAL_AMOUNTS,
    REQUIRED_AMOUNTS,
    UNITS_SCALE,
    SuperStatement,
    SuperStatementOption,
)
from finance360.core.models.valuation import Valuation
from finance360.core.money import INT64_MAX, Money, MoneyError, format_amount, parse_amount
from finance360.core.price import PRICE_SCALE
from finance360.core.rulepack import fy_label, fy_of

SUPER_ACCOUNT_TYPE = "super_fund_account"
CHECK_NAMES = ("roll", "outflows", "options")
OPTIONS_MAX = 30
#: The rounding tolerance per Investment option line, in minor units (one cent): funds round
#: units x price, so the lines can differ from the closing balance by that much each.
OPTION_TOLERANCE_MINOR = 1

#: The amounts that make up the outflow breakdown. The rebate subtracts; fees taken inside the
#: investment are stated but excluded, as they are already netted from the investment returns.
OUTFLOW_PARTS = (
    "contributions_tax",
    "fees_before_rebate",
    "rebate",
    "division_293_released",
    "other_withdrawals",
    "insurance_premiums",
)

CheckStatus = Literal["ok", "warn", "not_applicable"]


# --- inputs --------------------------------------------------------------------------------


@dataclass(frozen=True)
class OptionLineInput:
    name: str
    #: Value at 30 June as stated; a plain decimal string in the Account's currency.
    value: str
    #: A plain decimal string (at most 6 decimal places), or ``None`` when not stated.
    units: str | None = None
    unit_price: str | None = None


@dataclass(frozen=True)
class SuperStatementInput:
    period_start: date
    period_end: date
    #: The balance roll: plain decimal strings in the Account's currency.
    opening: str
    contributions_in: str
    investment_returns: str
    withdrawals: str
    closing: str
    #: Optional amounts: ``None`` or blank means "not stated", which is not zero.
    contributions_tax: str | None = None
    fees_before_rebate: str | None = None
    rebate: str | None = None
    fees_inside_investment: str | None = None
    division_293_released: str | None = None
    other_withdrawals: str | None = None
    insurance_premiums: str | None = None
    concessional_received: str | None = None
    non_concessional_received: str | None = None
    options: Sequence[OptionLineInput] = ()
    #: The names of the checks the owner has ticked as checked.
    ticked_checks: Sequence[str] = field(default_factory=tuple)


# --- the pure checks -----------------------------------------------------------------------


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: CheckStatus
    message: str


def roll_difference(
    opening: int, contributions_in: int, investment_returns: int, withdrawals: int, closing: int
) -> int:
    """Opening + in + returns - out, minus the stated closing: zero when the roll agrees."""
    return opening + contributions_in + investment_returns - withdrawals - closing


def outflow_parts_total(amounts: Mapping[str, int | None]) -> int | None:
    """The outflow breakdown summed (the rebate subtracts), or ``None`` when no part is stated."""
    stated = [amounts.get(name) for name in OUTFLOW_PARTS]
    if all(v is None for v in stated):
        return None
    total = 0
    for name in OUTFLOW_PARTS:
        value = amounts.get(name) or 0
        total += -value if name == "rebate" else value
    return total


def options_tolerance(line_count: int) -> int:
    return OPTION_TOLERANCE_MINOR * line_count


def evaluate_checks(
    amounts: Mapping[str, int | None], option_values: Sequence[int], currency: str
) -> list[CheckResult]:
    """The three checks over minor-unit amounts (named as in ``AMOUNTS``) and option values."""

    def money(minor: int) -> str:
        return f"{currency} {format_amount(Money(minor, currency))}"

    def need(name: str) -> int:
        return amounts[name] or 0

    results: list[CheckResult] = []

    diff = roll_difference(
        need("opening"),
        need("contributions_in"),
        need("investment_returns"),
        need("withdrawals"),
        need("closing"),
    )
    if diff == 0:
        results.append(
            CheckResult("roll", "ok", "The balance roll adds up to the closing balance.")
        )
    else:
        calculated = need("closing") + diff
        results.append(
            CheckResult(
                "roll",
                "warn",
                f"Opening + contributions in + investment returns - withdrawals is "
                f"{money(calculated)}, but the closing balance is {money(need('closing'))} "
                f"(difference {money(diff)}).",
            )
        )

    parts = outflow_parts_total(amounts)
    if parts is None:
        results.append(
            CheckResult("outflows", "not_applicable", "No outflow breakdown was entered.")
        )
    elif parts == need("withdrawals"):
        results.append(
            CheckResult("outflows", "ok", "The outflow breakdown adds up to withdrawals.")
        )
    else:
        results.append(
            CheckResult(
                "outflows",
                "warn",
                f"The outflow breakdown (the rebate subtracts) adds up to {money(parts)}, but "
                f"withdrawals are {money(need('withdrawals'))} "
                f"(difference {money(parts - need('withdrawals'))}). Fees taken inside the "
                "investment are not part of this sum.",
            )
        )

    if not option_values:
        results.append(CheckResult("options", "not_applicable", "No Investment options entered."))
    else:
        total = sum(option_values)
        tolerance = options_tolerance(len(option_values))
        gap = total - need("closing")
        if abs(gap) <= tolerance:
            results.append(
                CheckResult(
                    "options",
                    "ok",
                    "The Investment options add up to the closing balance"
                    + (" (within rounding)." if gap else "."),
                )
            )
        else:
            results.append(
                CheckResult(
                    "options",
                    "warn",
                    f"The Investment options add up to {money(total)}, but the closing balance is "
                    f"{money(need('closing'))} (difference {money(gap)}; funds round, so up to "
                    f"{money(tolerance)} is allowed).",
                )
            )
    return results


# --- parsing and validation ----------------------------------------------------------------


@dataclass(frozen=True)
class _OptionChecked:
    name: str
    units: Decimal | None
    unit_price: Decimal | None
    value: int


@dataclass(frozen=True)
class _Checked:
    account: Account
    period_start: date
    period_end: date
    #: Minor units by amount name; ``None`` for an optional amount not stated.
    amounts: dict[str, int | None]
    options: list[_OptionChecked]
    results: list[CheckResult]
    ticked: list[str]

    @property
    def fy(self) -> int:
        return fy_of(self.period_end)

    @property
    def currency(self) -> str:
        return self.account.currency


_DECIMAL = re.compile(r"[0-9]+(\.[0-9]+)?")


def _parse_scaled(text: str, scale: int) -> Decimal | str:
    """A non-negative plain decimal with at most ``scale`` places, or an error message."""
    clean = text.strip().replace(",", "")
    if clean.startswith("-"):
        return "Can't be negative."
    if not _DECIMAL.fullmatch(clean):
        return "Enter a number, e.g. 1234.5678."
    value = Decimal(clean)
    if -value.as_tuple().exponent > scale:  # type: ignore[operator]
        return f"At most {scale} decimal places."
    if value.scaleb(scale) > INT64_MAX:
        return "That is too large."
    return value


def _account(session: Session, account_id: int) -> Account:
    account = session.get(Account, account_id)
    if account is None:
        raise NotFoundError(f"Account {account_id} does not exist.")
    return account


def _require_super(account: Account) -> None:
    if account.account_type.key != SUPER_ACCOUNT_TYPE:
        raise ValidationError(
            [FieldError("account_id", "A Super statement belongs to a super fund Account.")]
        )


def _check_period(data: SuperStatementInput, errors: list[FieldError]) -> None:
    start, end = data.period_start, data.period_end
    if not (start.month == 7 and start.day == 1 and end == date(start.year + 1, 6, 30)):
        errors.append(
            FieldError(
                "period_start", "A Super statement covers one financial year, 1 July to 30 June."
            )
        )


def _parse_amounts(
    account: Account, data: SuperStatementInput, errors: list[FieldError]
) -> dict[str, int | None]:
    amounts: dict[str, int | None] = {}
    for name in AMOUNTS:
        raw: str | None = getattr(data, name)
        if raw is None or not raw.strip():
            if name in REQUIRED_AMOUNTS:
                errors.append(FieldError(name, "Enter an amount, or 0.00."))
            amounts[name] = None
            continue
        try:
            amounts[name] = parse_amount(raw, account.currency).amount_minor
        except MoneyError:
            errors.append(
                FieldError(
                    name,
                    f"Enter an amount in {account.currency}, e.g. 1234.56 (no more decimal "
                    "places than the currency has).",
                )
            )
            amounts[name] = None
    return amounts


def _parse_options(
    account: Account, data: SuperStatementInput, errors: list[FieldError]
) -> list[_OptionChecked]:
    if len(data.options) > OPTIONS_MAX:
        errors.append(FieldError("options", f"At most {OPTIONS_MAX} Investment options."))
        return []
    lines: list[_OptionChecked] = []
    for i, line in enumerate(data.options):
        prefix = f"options.{i}"
        name = re.sub(r"\s+", " ", line.name).strip()
        if not name:
            errors.append(FieldError(f"{prefix}.name", "Enter the Investment option's name."))
        elif len(name) > NAME_MAX:
            errors.append(FieldError(f"{prefix}.name", f"At most {NAME_MAX} characters."))
        value = 0
        try:
            value = parse_amount(line.value, account.currency).amount_minor
        except MoneyError:
            errors.append(
                FieldError(
                    f"{prefix}.value", f"Enter an amount in {account.currency}, e.g. 1234.56."
                )
            )
        scaled: list[Decimal | None] = []
        for label, raw, scale in (
            ("units", line.units, UNITS_SCALE),
            ("unit_price", line.unit_price, PRICE_SCALE),
        ):
            if raw is None or not raw.strip():
                scaled.append(None)
                continue
            parsed = _parse_scaled(raw, scale)
            if isinstance(parsed, str):
                errors.append(FieldError(f"{prefix}.{label}", parsed))
                scaled.append(None)
            else:
                scaled.append(parsed)
        lines.append(_OptionChecked(name, scaled[0], scaled[1], value))
    return lines


def _check(
    session: Session,
    account_id: int,
    data: SuperStatementInput,
    *,
    except_id: int | None = None,
    draft_only: bool = False,
) -> _Checked:
    """Validate the input (malformed blocks) and run the checks. Warnings are not raised here.

    ``draft_only`` is for checking figures as they are typed, which includes a correction of an
    existing year: the one-current-statement rule is for saving, so it is not applied."""
    account = _account(session, account_id)
    _require_super(account)
    errors: list[FieldError] = []
    _check_period(data, errors)
    amounts = _parse_amounts(account, data, errors)
    options = _parse_options(account, data, errors)
    if not errors and not draft_only:
        clash = session.scalar(
            select(SuperStatement.id).where(
                SuperStatement.account_id == account.id,
                SuperStatement.period_end == data.period_end,
                SuperStatement.superseded_at.is_(None),
                SuperStatement.id != (except_id or 0),
            )
        )
        if clash is not None:
            errors.append(
                FieldError(
                    "period_start",
                    f"There is already a Super statement for {fy_label(fy_of(data.period_end))}. "
                    "Correct that one instead.",
                )
            )
    if errors:
        raise ValidationError(errors)
    results = evaluate_checks(amounts, [o.value for o in options], account.currency)
    failing = [r.name for r in results if r.status == "warn"]
    ticked = [n for n in CHECK_NAMES if n in failing and n in data.ticked_checks]
    return _Checked(account, data.period_start, data.period_end, amounts, options, results, ticked)


def _require_ticks(checked: _Checked) -> None:
    missing = [
        FieldError(f"checks.{r.name}", r.message)
        for r in checked.results
        if r.status == "warn" and r.name not in checked.ticked
    ]
    if missing:
        raise ValidationError(missing)


def check_super_statement(
    session: Session, account_id: int, data: SuperStatementInput
) -> list[CheckResult]:
    """The checks for a draft, so the form can warn as figures are typed. Nothing is stored."""
    return _check(session, account_id, data, draft_only=True).results


# --- reads ---------------------------------------------------------------------------------


def get_super_statement(session: Session, statement_id: int) -> SuperStatement:
    statement = session.get(SuperStatement, statement_id)
    if statement is None:
        raise NotFoundError(f"Super statement {statement_id} does not exist.")
    return statement


def current_super_statement(session: Session, statement_id: int) -> SuperStatement:
    """The current version of a statement, following its corrections, so an address that was
    saved before a correction still opens the statement as it stands now. A removed statement
    (superseded with no replacement) is not found."""
    row = get_super_statement(session, statement_id)
    seen: set[int] = set()
    while row.superseded_by_id is not None and row.id not in seen:
        seen.add(row.id)
        row = get_super_statement(session, row.superseded_by_id)
    if row.superseded_at is not None:
        raise NotFoundError(f"Super statement {statement_id} was removed.")
    return row


def list_super_statements(session: Session, account_id: int) -> list[SuperStatement]:
    """The Account's current statements, newest financial year first."""
    _account(session, account_id)
    return list(
        session.scalars(
            select(SuperStatement)
            .where(SuperStatement.account_id == account_id, SuperStatement.superseded_at.is_(None))
            .order_by(SuperStatement.period_end.desc(), SuperStatement.id.desc())
        )
    )


def ticked_checks(statement: SuperStatement) -> list[str]:
    return list(json.loads(statement.checks_ticked_json))


@dataclass(frozen=True)
class SuperYear:
    """One Account's financial year as the fund stated it: what tax and returns read (ADR-0025)."""

    statement_id: int
    account_id: int
    financial_year: int
    period_start: date
    period_end: date
    opening: Money
    contributions_in: Money
    investment_returns: Money
    withdrawals: Money
    closing: Money
    #: ``None`` means the statement did not state it (not zero).
    contributions_tax: Money | None
    fees_before_rebate: Money | None
    rebate: Money | None
    fees_inside_investment: Money | None
    division_293_released: Money | None
    other_withdrawals: Money | None
    insurance_premiums: Money | None
    concessional_received: Money | None
    non_concessional_received: Money | None


def list_super_years(
    session: Session, *, account_id: int | None = None, financial_year: int | None = None
) -> list[SuperYear]:
    """The current Super statements as frozen ``SuperYear`` values, oldest year first."""
    query = (
        select(SuperStatement)
        .where(SuperStatement.superseded_at.is_(None))
        .order_by(SuperStatement.period_end, SuperStatement.account_id)
    )
    if account_id is not None:
        query = query.where(SuperStatement.account_id == account_id)
    years: list[SuperYear] = []
    for s in session.scalars(query):
        fy = fy_of(s.period_end)
        if financial_year is not None and fy != financial_year:
            continue
        zero = Money.zero(s.currency)
        years.append(
            SuperYear(
                statement_id=s.id,
                account_id=s.account_id,
                financial_year=fy,
                period_start=s.period_start,
                period_end=s.period_end,
                opening=s.amount("opening") or zero,
                contributions_in=s.amount("contributions_in") or zero,
                investment_returns=s.amount("investment_returns") or zero,
                withdrawals=s.amount("withdrawals") or zero,
                closing=s.amount("closing") or zero,
                contributions_tax=s.amount("contributions_tax"),
                fees_before_rebate=s.amount("fees_before_rebate"),
                rebate=s.amount("rebate"),
                fees_inside_investment=s.amount("fees_inside_investment"),
                division_293_released=s.amount("division_293_released"),
                other_withdrawals=s.amount("other_withdrawals"),
                insurance_premiums=s.amount("insurance_premiums"),
                concessional_received=s.amount("concessional_received"),
                non_concessional_received=s.amount("non_concessional_received"),
            )
        )
    return years


# --- audit snapshots -----------------------------------------------------------------------


def _snapshot(statement: SuperStatement) -> dict[str, Any]:
    def money(name: str) -> dict[str, Any] | None:
        value = statement.amount(name)
        return None if value is None else {"minor": value.amount_minor, "ccy": value.currency}

    return {
        "id": statement.id,
        "account_id": statement.account_id,
        "financial_year": fy_of(statement.period_end),
        "period_start": statement.period_start.isoformat(),
        "period_end": statement.period_end.isoformat(),
        **{name: money(name) for name in AMOUNTS},
        "options": [
            {
                "name": o.name,
                "units": None if o.units is None else str(o.units),
                "unit_price": None if o.unit_price is None else str(o.unit_price),
                "value": {"minor": o.value_minor, "ccy": statement.currency},
            }
            for o in statement.options
        ],
        "valuation_id": statement.valuation_id,
        "checks_ticked": ticked_checks(statement),
    }


def _describe(account: Account, statement: SuperStatement) -> str:
    return f"Super statement for '{account.name}', {fy_label(fy_of(statement.period_end))}"


# --- the Valuation that goes with a statement ----------------------------------------------


def _valuation_input(checked: _Checked) -> valuations.ValuationInput:
    closing = Money(checked.amounts["closing"] or 0, checked.currency)
    return valuations.ValuationInput(
        as_at=checked.period_end,
        amount=format_amount(closing),
        source="statement",
        provenance_note=f"Super statement for {fy_label(checked.fy)}",
        currency=checked.currency,
    )


def _remap(error: ValidationError) -> ValidationError:
    """Show the Valuation's refusals against the statement's own fields."""
    names = {"as_at": "period_end", "amount": "closing"}
    clash = (
        "There is already a statement Valuation on this 30 June (typed on the Account's "
        "Valuations). Correct or remove that one first, then save the Super statement."
    )
    return ValidationError(
        [
            FieldError(
                names.get(e.field, e.field),
                clash if e.message.startswith("There is already a") else e.message,
            )
            for e in error.errors
        ]
    )


def current_valuation(session: Session, valuation_id: int) -> Valuation | None:
    """The Valuation a statement recorded, following its corrections; ``None`` if it was removed."""
    seen: set[int] = set()
    valuation: Valuation | None = session.get(Valuation, valuation_id)
    while valuation is not None and valuation.superseded_at is not None:
        if valuation.id in seen or valuation.superseded_by_id is None:
            return None
        seen.add(valuation.id)
        valuation = session.get(Valuation, valuation.superseded_by_id)
    return valuation


# --- mutations -----------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)  # naive UTC, as every timestamp here


def _insert(
    session: Session, checked: _Checked, valuation_id: int, now: datetime
) -> SuperStatement:
    statement = SuperStatement(
        account_id=checked.account.id,
        period_start=checked.period_start,
        period_end=checked.period_end,
        currency=checked.currency,
        valuation_id=valuation_id,
        checks_ticked_json=json.dumps(checked.ticked),
        recorded_at=now,
        **{f"{name}_minor": checked.amounts[name] for name in AMOUNTS},
    )
    statement.options = [
        SuperStatementOption(
            position=i,
            name=o.name,
            units=o.units,
            unit_price=o.unit_price,
            value_minor=o.value,
        )
        for i, o in enumerate(checked.options)
    ]
    session.add(statement)
    session.flush()
    return statement


def create_super_statement(
    session: Session,
    context: AuditContext,
    account_id: int,
    data: SuperStatementInput,
    *,
    today: date | None = None,
) -> SuperStatement:
    """Record a Super statement and its 30 June Valuation together."""
    checked = _check(session, account_id, data)
    _require_ticks(checked)
    try:
        valuation = valuations.add_valuation(
            session, context, checked.account.id, _valuation_input(checked), today=today
        )
    except ValidationError as error:
        raise _remap(error) from None
    statement = _insert(session, checked, valuation.id, _now())
    audit.record(
        session,
        context,
        entity_type="super_statement",
        entity_id=statement.id,
        action="create",
        after=_snapshot(statement),
        summary=f"Added {_describe(checked.account, statement)}",
    )
    return statement


def _same(old: SuperStatement, checked: _Checked) -> bool:
    return (
        old.period_start == checked.period_start
        and old.period_end == checked.period_end
        and all(getattr(old, f"{n}_minor") == checked.amounts[n] for n in AMOUNTS)
        and ticked_checks(old) == checked.ticked
        and [(o.name, o.units, o.unit_price, o.value_minor) for o in old.options]
        == [(o.name, o.units, o.unit_price, o.value) for o in checked.options]
    )


def correct_super_statement(
    session: Session,
    context: AuditContext,
    statement_id: int,
    data: SuperStatementInput,
    *,
    today: date | None = None,
) -> SuperStatement:
    """Replace a current statement: the old one is superseded (kept, with its option lines) and
    its Valuation is corrected with it. The financial year can't change; remove and add instead."""
    old = get_super_statement(session, statement_id)
    if old.superseded_at is not None:
        raise ValidationError(
            [FieldError("id", "Only a current Super statement can be corrected.")]
        )
    if (data.period_start, data.period_end) != (old.period_start, old.period_end):
        raise ValidationError(
            [
                FieldError(
                    "period_start",
                    "A correction keeps the financial year. To change it, remove this statement "
                    "and add another.",
                )
            ]
        )
    checked = _check(session, old.account_id, data, except_id=old.id)
    _require_ticks(checked)
    if _same(old, checked):
        return old
    before = _snapshot(old)
    try:
        existing = current_valuation(session, old.valuation_id)
        if existing is None:  # removed by hand since: record it again
            valuation = valuations.add_valuation(
                session, context, checked.account.id, _valuation_input(checked), today=today
            )
        else:
            valuation = valuations.correct_valuation(
                session, context, existing.id, _valuation_input(checked), today=today
            )
    except ValidationError as error:
        raise _remap(error) from None
    now = _now()
    old.superseded_at = now  # frees the one-current slot before the replacement is inserted
    session.flush()
    new = _insert(session, checked, valuation.id, now)
    old.superseded_by_id = new.id
    session.flush()
    audit.record(
        session,
        context,
        entity_type="super_statement",
        entity_id=new.id,
        action="supersede",
        before=before,
        after=_snapshot(new),
        summary=f"Corrected {_describe(checked.account, new)}",
    )
    return new


def remove_super_statement(
    session: Session, context: AuditContext, statement_id: int
) -> SuperStatement:
    """Supersede a current statement with no replacement, and its Valuation with it."""
    old = get_super_statement(session, statement_id)
    if old.superseded_at is not None:
        raise ValidationError([FieldError("id", "Only a current Super statement can be removed.")])
    account = _account(session, old.account_id)
    before = _snapshot(old)
    existing = current_valuation(session, old.valuation_id)
    if existing is not None:
        valuations.remove_valuation(session, context, existing.id)
    old.superseded_at = _now()
    session.flush()
    audit.record(
        session,
        context,
        entity_type="super_statement",
        entity_id=old.id,
        action="supersede",
        before=before,
        summary=f"Removed {_describe(account, old)}",
    )
    return old


# --- First run -----------------------------------------------------------------------------

FIXTURE_CONTEXT = valuations.FIXTURE_CONTEXT


def load_fixture_super_statements(session: Session, data_dir: Path) -> int:
    """Create the fixture's Super statements through the service, once, while there are none.

    Reads the top-level ``super_statements`` of ``<data_dir>/household.json``; each entry names its
    Account. Every check in the fixture agrees, so none needs a tick.
    """
    from finance360.capabilities.household import service as household

    path = data_dir / household.FIXTURE_FILENAME
    if not path.is_file() or session.scalar(select(SuperStatement.id).limit(1)) is not None:
        return 0
    entries = json.loads(path.read_text(encoding="utf-8")).get("super_statements", [])
    ids = {a.name: a.id for a in session.scalars(select(Account))}
    created = 0
    for entry in entries:
        if entry["account"] not in ids:
            continue
        end = date.fromisoformat(f"{entry['financial_year']}-06-30")
        data = SuperStatementInput(
            period_start=date(end.year - 1, 7, 1),
            period_end=end,
            options=[OptionLineInput(**o) for o in entry.get("options", [])],
            **{k: v for k, v in entry.items() if k in AMOUNTS},
        )
        create_super_statement(session, FIXTURE_CONTEXT, ids[entry["account"]], data)
        created += 1
    return created


__all__ = [
    "CHECK_NAMES",
    "OPTIONAL_AMOUNTS",
    "CheckResult",
    "OptionLineInput",
    "SuperStatementInput",
    "SuperYear",
    "check_super_statement",
    "correct_super_statement",
    "create_super_statement",
    "current_valuation",
    "evaluate_checks",
    "list_super_statements",
    "list_super_years",
    "load_fixture_super_statements",
    "outflow_parts_total",
    "remove_super_statement",
    "roll_difference",
    "ticked_checks",
]
