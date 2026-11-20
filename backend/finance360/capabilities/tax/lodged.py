"""Lodged results and the return worksheet (ADR-0019 section 8). Impure: database and audit.

A **Lodged result** is a Member's assessed outcome for a financial year, entered from a notice of
assessment (entity type ``tax_lodged_result``). Items are result keys (``compare.RESULT_KEYS``) to
amounts, plus named ``other`` lines for anything the engine has no key for. An amended notice
**supersedes** the earlier row as a Valuation or a Super statement is: the earlier row is kept, set
aside with ``superseded_at`` and ``superseded_by_id``, and stays retrievable through ``history``.
Rows are never updated or deleted (triggers, migration 0032). There is no removal: a wrong entry is
amended.

``worksheet`` puts the engine's estimate beside the current Lodged result (``compare_to_lodged``).
The estimate is whole-Household (the family tests need both Members), so it is calculated for them
and the chosen Member's result is compared. If it cannot be calculated (no Rule figures for the
year, say) the worksheet still shows the Lodged result and the reasons.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from finance360.capabilities.household import service as household
from finance360.capabilities.tax import compare, saved
from finance360.capabilities.tax import input as tinput
from finance360.capabilities.tax.engine import MemberResult
from finance360.core import audit
from finance360.core.audit import AuditContext
from finance360.core.errors import FieldError, NotFoundError, ValidationError
from finance360.core.models.household import Member
from finance360.core.models.tax_lodged_result import OTHER_NAME_MAX, TaxLodgedResult
from finance360.core.money import BASE_CURRENCY, Money, MoneyError, parse_amount
from finance360.core.rulepack import fy_label

ENTITY = "tax_lodged_result"
EARLIEST_FY = 1990
LATEST_FY = 2200
MAX_OTHER = 20
FIXTURE_FILENAME = "lodged_results.json"
FIXTURE_CONTEXT = AuditContext("system", "fixture", "fixture")


@dataclass(frozen=True)
class OtherInput:
    name: str
    #: Decimal text in AUD, as printed on the notice.
    amount: str


@dataclass(frozen=True)
class LodgedInput:
    member_id: int
    financial_year: int
    assessed_on: date | None
    #: Result key to decimal text in AUD. A blank amount means "not on the notice" and is left out.
    items: Mapping[str, str]
    other: Sequence[OtherInput] = ()


@dataclass(frozen=True)
class LodgedView:
    id: int
    member_id: int
    financial_year: int
    assessed_on: date
    result: compare.LodgedResult
    recorded_at: datetime
    #: ``None`` for the current notice.
    superseded_at: datetime | None
    superseded_by_id: int | None

    @property
    def current(self) -> bool:
        return self.superseded_at is None


@dataclass(frozen=True)
class Worksheet:
    member_id: int
    member_name: str
    financial_year: int
    dependants: int
    #: The current Lodged result, or ``None`` when none is entered.
    current: LodgedView | None
    #: Every notice for the Member and year, newest first: the current one, then those it replaced.
    history: tuple[LodgedView, ...]
    #: The Member's estimate, or ``None`` when it could not be calculated (see ``problems``).
    estimate: MemberResult | None
    incomplete: bool
    engine_version: int | None
    rule_pack_version: str | None
    problems: tuple[str, ...]
    comparison: compare.LodgedComparison | None


# --- storage shape ---------------------------------------------------------------------------


def _dump(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _items_out(items: Sequence[tuple[str, Money]]) -> str:
    return _dump({key: tinput.money_out(amount) for key, amount in items})


def _other_out(other: Sequence[compare.OtherLine]) -> str:
    return _dump(
        [
            {"amount": tinput.money_out(o.amount), "name": o.name}
            for o in other
            if o.amount is not None
        ]
    )


def _view(row: TaxLodgedResult) -> LodgedView:
    order = {key: i for i, key in enumerate(compare.RESULT_KEYS)}
    items = sorted(
        (
            (key, tinput.money_in(raw, key))
            for key, raw in json.loads(row.items_json).items()
            if key in order
        ),
        key=lambda item: order[item[0]],
    )
    other = tuple(
        compare.OtherLine(o["name"], tinput.money_in(o["amount"], o["name"]))
        for o in json.loads(row.other_json)
    )
    return LodgedView(
        id=row.id,
        member_id=row.member_id,
        financial_year=row.financial_year,
        assessed_on=row.assessed_on,
        result=compare.LodgedResult(tuple(items), other),
        recorded_at=row.recorded_at,
        superseded_at=row.superseded_at,
        superseded_by_id=row.superseded_by_id,
    )


def _snapshot(row: TaxLodgedResult) -> dict[str, Any]:
    return {
        "id": row.id,
        "member_id": row.member_id,
        "financial_year": row.financial_year,
        "assessed_on": row.assessed_on.isoformat(),
        "items": json.loads(row.items_json),
        "other": json.loads(row.other_json),
    }


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)  # naive UTC, as every timestamp here


# --- reads -----------------------------------------------------------------------------------


def get_lodged_result(session: Session, lodged_id: int) -> LodgedView:
    row = session.get(TaxLodgedResult, lodged_id)
    if row is None:
        raise NotFoundError(f"Lodged result {lodged_id} does not exist.")
    return _view(row)


def history(session: Session, member_id: int, financial_year: int) -> list[LodgedView]:
    """Every notice for the Member and year, newest first (the current one, then those it
    superseded). The earlier rows are kept, so an amendment never loses what was entered."""
    rows = session.scalars(
        select(TaxLodgedResult)
        .where(
            TaxLodgedResult.member_id == member_id,
            TaxLodgedResult.financial_year == financial_year,
        )
        .order_by(TaxLodgedResult.id.desc())
    )
    return [_view(r) for r in rows]


def current_lodged_result(
    session: Session, member_id: int, financial_year: int
) -> LodgedView | None:
    return next((v for v in history(session, member_id, financial_year) if v.current), None)


def list_current(session: Session, financial_year: int | None = None) -> list[LodgedView]:
    """The current Lodged results, newest year first."""
    query = (
        select(TaxLodgedResult)
        .where(TaxLodgedResult.superseded_at.is_(None))
        .order_by(TaxLodgedResult.financial_year.desc(), TaxLodgedResult.member_id)
    )
    if financial_year is not None:
        query = query.where(TaxLodgedResult.financial_year == financial_year)
    return [_view(r) for r in session.scalars(query)]


# --- writes ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Checked:
    member: Member
    financial_year: int
    assessed_on: date
    items: tuple[tuple[str, Money], ...]
    other: tuple[compare.OtherLine, ...]


def _amount(text: str, field: str, errors: list[FieldError]) -> Money | None:
    try:
        return parse_amount(text, BASE_CURRENCY)
    except MoneyError:
        errors.append(FieldError(field, "Enter an amount in dollars and cents, such as 1234.56."))
        return None


def _check(session: Session, data: LodgedInput) -> _Checked:
    errors: list[FieldError] = []
    member = session.get(Member, data.member_id)
    if member is None:
        errors.append(FieldError("member_id", f"Member {data.member_id} does not exist."))
    if not EARLIEST_FY <= data.financial_year <= LATEST_FY:
        errors.append(
            FieldError("financial_year", f"Enter a year from {EARLIEST_FY} to {LATEST_FY}.")
        )
    if data.assessed_on is None:
        errors.append(FieldError("assessed_on", "Enter the date on the notice."))
    items: list[tuple[str, Money]] = []
    for key, text in data.items.items():
        if key not in compare.RESULT_KEYS:
            errors.append(FieldError(f"items.{key}", f"{key} is not an item the engine models."))
        elif text.strip():
            amount = _amount(text, f"items.{key}", errors)
            if amount is not None:
                items.append((key, amount))
    other: list[compare.OtherLine] = []
    if len(data.other) > MAX_OTHER:
        errors.append(FieldError("other", f"Enter at most {MAX_OTHER} other lines."))
    names: set[str] = set()
    for i, line in enumerate(data.other):
        name = line.name.strip()
        where = f"other.{i}"
        if not name:
            errors.append(FieldError(f"{where}.name", "Name the line as the notice does."))
        elif len(name) > OTHER_NAME_MAX:
            errors.append(FieldError(f"{where}.name", f"Use at most {OTHER_NAME_MAX} characters."))
        elif name.casefold() in names:
            errors.append(FieldError(f"{where}.name", f"There is already a line named '{name}'."))
        names.add(name.casefold())
        amount = _amount(line.amount, f"{where}.amount", errors)
        if amount is not None and name:
            other.append(compare.OtherLine(name, amount))
    if not errors and not items and not other:
        errors.append(FieldError("items", "Enter at least one amount from the notice."))
    if errors or member is None or data.assessed_on is None:
        raise ValidationError(errors)
    return _Checked(member, data.financial_year, data.assessed_on, tuple(items), tuple(other))


def _describe(member: Member, row: TaxLodgedResult) -> str:
    return (
        f"Lodged result for {member.name}, FY {fy_label(row.financial_year)}, "
        f"assessed {row.assessed_on.isoformat()}"
    )


def _insert(session: Session, checked: _Checked, now: datetime) -> TaxLodgedResult:
    row = TaxLodgedResult(
        member_id=checked.member.id,
        financial_year=checked.financial_year,
        assessed_on=checked.assessed_on,
        items_json=_items_out(checked.items),
        other_json=_other_out(checked.other),
        recorded_at=now,
    )
    session.add(row)
    session.flush()
    return row


def create_lodged_result(session: Session, context: AuditContext, data: LodgedInput) -> LodgedView:
    """Enter the first notice for a Member and year. A later notice goes through
    ``amend_lodged_result``, so the earlier one is superseded, never overwritten."""
    checked = _check(session, data)
    if current_lodged_result(session, checked.member.id, checked.financial_year) is not None:
        raise ValidationError(
            [
                FieldError(
                    "financial_year",
                    f"There is already a Lodged result for {checked.member.name}, "
                    f"FY {fy_label(checked.financial_year)}. Enter the amended notice instead.",
                )
            ]
        )
    row = _insert(session, checked, _now())
    audit.record(
        session,
        context,
        entity_type=ENTITY,
        entity_id=row.id,
        action="create",
        after=_snapshot(row),
        summary=f"Entered {_describe(checked.member, row)}",
    )
    return _view(row)


def amend_lodged_result(
    session: Session, context: AuditContext, lodged_id: int, data: LodgedInput
) -> LodgedView:
    """Enter an amended notice: the current row is superseded (kept) and the new one replaces it.
    The Member and the financial year stay; enter a new notice for another year."""
    old = session.get(TaxLodgedResult, lodged_id)
    if old is None:
        raise NotFoundError(f"Lodged result {lodged_id} does not exist.")
    if old.superseded_at is not None:
        raise ValidationError([FieldError("id", "Only the current Lodged result can be amended.")])
    if (data.member_id, data.financial_year) != (old.member_id, old.financial_year):
        raise ValidationError(
            [
                FieldError(
                    "financial_year", "An amended notice keeps the Member and the financial year."
                )
            ]
        )
    checked = _check(session, data)
    before = _snapshot(old)
    now = _now()
    old.superseded_at = now  # frees the one-current slot before the replacement is inserted
    session.flush()
    new = _insert(session, checked, now)
    old.superseded_by_id = new.id
    session.flush()
    audit.record(
        session,
        context,
        entity_type=ENTITY,
        entity_id=new.id,
        action="supersede",
        before=before,
        after=_snapshot(new),
        summary=f"Entered an amended {_describe(checked.member, new)}",
    )
    return _view(new)


# --- the worksheet ---------------------------------------------------------------------------


def _pack_label(body: saved.Body) -> str | None:
    return next((p.detail for p in body.input.provenance if p.key == "rule_pack"), None)


def worksheet(
    session: Session, member_id: int, financial_year: int, dependants: int = 0
) -> Worksheet:
    """The engine's estimate beside the current Lodged result, with every difference and every
    lodged item the engine cannot match; the superseded notices are in ``history``."""
    member = session.get(Member, member_id)
    if member is None:
        raise NotFoundError(f"Member {member_id} does not exist.")
    notices = history(session, member_id, financial_year)
    current = next((n for n in notices if n.current), None)
    ids = [member_id] + [m.id for m in household.list_members(session) if m.id != member_id]
    estimate: MemberResult | None = None
    problems: list[str] = []
    incomplete, engine_version, pack_version = False, None, None
    try:
        body = saved.calculate(
            session,
            financial_year=financial_year,
            dependants=dependants,
            member_ids=ids[: tinput.MAX_MEMBERS],
        )
    except ValidationError as error:
        problems = [e.message for e in error.errors]
    else:
        estimate = next(m for m in body.result.members if m.member_id == member_id)
        incomplete = bool(body.result.unmodelled)
        engine_version = body.result.engine_version
        pack_version = _pack_label(body)
    comparison = (
        compare.compare_to_lodged(estimate, current.result)
        if estimate is not None and current is not None
        else None
    )
    return Worksheet(
        member_id=member_id,
        member_name=member.name,
        financial_year=financial_year,
        dependants=dependants,
        current=current,
        history=tuple(notices),
        estimate=estimate,
        incomplete=incomplete,
        engine_version=engine_version,
        rule_pack_version=pack_version,
        problems=tuple(problems),
        comparison=comparison,
    )


# --- Member delete guard ---------------------------------------------------------------------


def member_delete_guard(session: Session, member_id: int) -> str | None:
    """Block deleting a Member who has a Lodged result (it is kept, so the Member must be)."""
    held = session.scalar(
        select(TaxLodgedResult.id).where(TaxLodgedResult.member_id == member_id).limit(1)
    )
    return "they have a Lodged result" if held else None


def register() -> None:
    """Register this guard with ``household``. Idempotent; call at app start."""
    household.register_member_delete_guard(member_delete_guard)


# --- First run -------------------------------------------------------------------------------


def load_fixture_lodged_results(session: Session, data_dir: Path) -> int:
    """Enter the fixture's Lodged results through the service, once, while there are none.

    Reads ``<data_dir>/lodged_results.json`` (a file of its own, so the household file's counts do
    not move): ``lodged_results``, each a ``member`` name, ``financial_year``, ``assessed_on``,
    ``items`` (result key to amount text) and ``other`` lines, in the order entered: an entry with
    the same member and year as an earlier one is that notice's amendment. Needs the Household.
    """
    path = data_dir / FIXTURE_FILENAME
    if not path.is_file() or list_current(session):
        return 0
    data = json.loads(path.read_text(encoding="utf-8"))
    ids = {m.name: m.id for m in household.list_members(session)}
    count = 0
    for entry in data.get("lodged_results", []):
        member_id, year = ids[entry["member"]], entry["financial_year"]
        given = LodgedInput(
            member_id,
            year,
            date.fromisoformat(entry["assessed_on"]),
            entry.get("items", {}),
            [OtherInput(o["name"], o["amount"]) for o in entry.get("other", [])],
        )
        earlier = current_lodged_result(session, member_id, year)
        if earlier is None:
            create_lodged_result(session, FIXTURE_CONTEXT, given)
        else:
            amend_lodged_result(session, FIXTURE_CONTEXT, earlier.id, given)
        count += 1
    return count
