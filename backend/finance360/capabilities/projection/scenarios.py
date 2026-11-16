"""Scenario and Assumption services (ADR-0016 sections 1, 2, 5 and 6).

Reads, writes and resolution over the tables (``assumption``, ``scenario`` and, for the spending
plan, ``spending_phase``, which is read and written as a whole list, ADR-0017 section 6).
Mutating functions take the caller's ``Session`` and an explicit ``AuditContext``, write the audit
record in that session and flush, never commit.
The layering rule itself is the pure ``assumptions.resolve``; this module only loads rows for it.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from finance360.capabilities.household import service as household
from finance360.capabilities.projection import assumptions, income_streams, spending
from finance360.capabilities.projection.assumptions import Value
from finance360.core import audit, rulepack
from finance360.core.audit import AuditContext
from finance360.core.errors import FieldError, NotFoundError, ValidationError
from finance360.core.models.projection_run import ProjectionRun
from finance360.core.models.scenario import Assumption, Scenario, SpendingPhase
from finance360.core.money import Money, format_amount
from finance360.core.rate import Rate
from finance360.core.rulepack import Age

NAME_MAX = 100
NOTES_MAX = 500
HOUSEHOLD_LAYER = "the Household defaults"


@dataclass(frozen=True)
class Change:
    """One edit to a layer. A ``value`` of None removes the row: the Scenario inherits again."""

    key: str
    scope_type: str
    scope_ref: str
    value: str | None


@dataclass(frozen=True)
class Review:
    """A Household default that changed while these Scenarios override it, so they did not move."""

    key: str
    scope_type: str
    scope_ref: str
    scenarios: tuple[str, ...]


# --- rows and values -----------------------------------------------------------------------


def _value_of(row: Assumption) -> Value:
    if row.value_rate_ppm is not None:
        return Rate(row.value_rate_ppm)
    if row.value_int is not None:
        spec = assumptions.spec_of(row.key)
        if spec is not None and spec.kind == assumptions.AGE_KIND:
            return Age.from_months(row.value_int)  # a Rule pack age override, in months
        if spec is not None and spec.kind == "choice":
            return spec.choices[row.value_int]  # held as the word's place in the key's choices
        return row.value_int
    if row.value_minor is None or row.value_ccy is None:
        raise ValueError(f"Assumption {row.id} stores no value")  # the CHECK forbids this
    return Money(row.value_minor, row.value_ccy)


def store_value(row: Assumption, value: Value) -> None:
    row.value_rate_ppm = value.ppm if isinstance(value, Rate) else None
    if isinstance(value, str):
        spec = assumptions.spec_of(row.key)
        if spec is None or value not in spec.choices:
            raise ValueError(f"{value!r} is not a choice for {row.key!r}")
        row.value_int = spec.choices.index(value)
    elif isinstance(value, Age):
        row.value_int = value.total_months
    else:
        row.value_int = value if isinstance(value, int) else None
    row.value_minor = value.amount_minor if isinstance(value, Money) else None
    row.value_ccy = value.currency if isinstance(value, Money) else None


def to_row(row: Assumption) -> assumptions.Row:
    return assumptions.Row(row.key, row.scope_type, row.scope_ref, _value_of(row))


def _audit_value(value: Value) -> Any:
    """ADR-0016 s5: ``{minor, ccy}`` for Money, a percent string for a Rate, an int."""
    if isinstance(value, Money):
        return {"minor": value.amount_minor, "ccy": value.currency}
    if isinstance(value, Rate):
        return value.to_percent()
    if isinstance(value, Age):
        return str(value)
    return value


def _label(key: str, scope_ref: str) -> str:
    spec = assumptions.spec_of(key)
    base = spec.label if spec is not None else key
    return f"{base} ({scope_ref})" if scope_ref else base


# --- reads ---------------------------------------------------------------------------------


def list_scenarios(session: Session) -> list[Scenario]:
    return list(session.scalars(select(Scenario).order_by(Scenario.id)))


def get_scenario(session: Session, scenario_id: int) -> Scenario:
    scenario = session.get(Scenario, scenario_id)
    if scenario is None:
        raise NotFoundError("Scenario not found.")
    return scenario


def layer_rows(session: Session, scenario_id: int | None) -> list[Assumption]:
    """The rows of one layer: a Scenario's overrides, or (None) the Household defaults."""
    where = (
        Assumption.scenario_id.is_(None)
        if scenario_id is None
        else (Assumption.scenario_id == scenario_id)
    )
    return list(session.scalars(select(Assumption).where(where).order_by(Assumption.id)))


def phase_rows(session: Session, scenario_id: int | None) -> list[SpendingPhase]:
    """The spending phase rows of one layer (None is the Household default), in list order."""
    where = (
        SpendingPhase.scenario_id.is_(None)
        if scenario_id is None
        else (SpendingPhase.scenario_id == scenario_id)
    )
    rows = session.scalars(select(SpendingPhase).where(where))
    return sorted(rows, key=lambda r: (spending.ANCHORS.index(r.anchor), r.offset_years))


def _phase_of(row: SpendingPhase) -> spending.Phase:
    return spending.Phase(row.anchor, row.offset_years, Money(row.amount_minor, row.amount_ccy))


def layer_phases(session: Session, scenario_id: int | None) -> list[spending.Phase]:
    """The spending phases one layer states, as plain values."""
    return [_phase_of(r) for r in phase_rows(session, scenario_id)]


def scenarios_with_own_spending(session: Session) -> dict[int, str]:
    """Scenario id -> name, for each Scenario that states its own spending phases."""
    rows = session.execute(
        select(Scenario.id, Scenario.name)
        .where(Scenario.id.in_(select(SpendingPhase.scenario_id).distinct()))
        .order_by(Scenario.id)
    )
    return {int(i): name for i, name in rows}


def override_counts(session: Session) -> dict[int, int]:
    rows = session.execute(
        select(Assumption.scenario_id, func.count())
        .where(Assumption.scenario_id.is_not(None))
        .group_by(Assumption.scenario_id)
    )
    return {int(sid): n for sid, n in rows if sid is not None}


def overriding_scenarios(
    session: Session, key: str, scope_type: str, scope_ref: str
) -> tuple[str, ...]:
    """Names of the Scenarios that state their own value for this key at this scope."""
    names = session.scalars(
        select(Scenario.name)
        .join(Assumption, Assumption.scenario_id == Scenario.id)
        .where(
            Assumption.key == key,
            Assumption.scope_type == scope_type,
            Assumption.scope_ref == scope_ref,
        )
        .order_by(Scenario.id)
    )
    return tuple(names)


def _rule_keys() -> tuple[str, ...]:
    """The registered pack's overridable keys (``rule.<key>`` Assumptions, ADR-0018 section 5)."""
    pack = rulepack.get(assumptions.JURISDICTION)
    return pack.keys if pack is not None else ()


def resolve_for(session: Session, scenario_id: int) -> tuple[Scenario, assumptions.Resolved]:
    """Resolve a Scenario over the Household defaults, for the current Members.

    A key with no value in any layer is a validation error naming it (there is no silent zero).
    """
    scenario = get_scenario(session, scenario_id)
    members = household.list_members(session)
    names = {str(m.id): m.name for m in members}

    def what(m: assumptions.Missing) -> str:
        if m.key == spending.FIELD:
            return "Spending has no phases."
        return f"{_label(m.key, names.get(m.scope_ref, m.scope_ref))} has no value."

    try:
        resolved = assumptions.resolve(
            [to_row(r) for r in layer_rows(session, None)],
            [to_row(r) for r in layer_rows(session, scenario_id)],
            member_ids=[m.id for m in members],
            rule_keys=_rule_keys(),
            wage_member_ids=income_streams.salary_member_ids(session),
            default_phases=layer_phases(session, None),
            override_phases=layer_phases(session, scenario_id),
        )
    except assumptions.UnresolvedError as error:
        raise ValidationError(
            [
                FieldError(
                    m.key if not m.scope_ref else f"{m.key}.{m.scope_ref}",
                    f"{what(m)} Set it in {HOUSEHOLD_LAYER} or in {scenario.name}.",
                )
                for m in error.missing
            ]
        ) from None
    return scenario, resolved


# --- Scenario writes -----------------------------------------------------------------------


def _clean_scenario(
    session: Session, name: str, notes: str | None, *, own_id: int | None = None
) -> tuple[str, str | None]:
    errors: list[FieldError] = []
    clean = name.strip()
    if not clean:
        errors.append(FieldError("name", "Name is required."))
    elif len(clean) > NAME_MAX:
        errors.append(FieldError("name", f"Name must be at most {NAME_MAX} characters."))
    else:
        clash = session.scalar(
            select(Scenario.id).where(func.lower(Scenario.name) == clean.lower())
        )
        if clash is not None and clash != own_id:
            errors.append(FieldError("name", "A Scenario with this name already exists."))
    note = (notes or "").strip() or None
    if note is not None and len(note) > NOTES_MAX:
        errors.append(FieldError("notes", f"Notes must be at most {NOTES_MAX} characters."))
    if errors:
        raise ValidationError(errors)
    return clean, note


def drawdown_order_of(scenario: Scenario) -> tuple[str, ...] | None:
    """The Scenario's own Drawdown policy, or None for the default order (ADR-0016 s3)."""
    if scenario.drawdown_order is None:
        return None
    return tuple(json.loads(scenario.drawdown_order))


def _scenario_snapshot(scenario: Scenario) -> dict[str, Any]:
    order = drawdown_order_of(scenario)
    return {
        "key": scenario.key,
        "name": scenario.name,
        "notes": scenario.notes,
        "drawdown_order": list(order) if order is not None else None,
    }


def _phase_snapshot(row: SpendingPhase) -> dict[str, Any]:
    return {
        "anchor": row.anchor,
        "offset_years": row.offset_years,
        "annual_amount": {"minor": row.amount_minor, "ccy": row.amount_ccy},
    }


def _order_text(order: Sequence[str] | None) -> str:
    return "the default order" if order is None else ", ".join(order)


def create_scenario(
    session: Session,
    context: AuditContext,
    name: str,
    notes: str | None = None,
    clone_from: int | None = None,
) -> Scenario:
    """Add a Scenario, optionally copying another's override rows. There is no parent link."""
    clean, note = _clean_scenario(session, name, notes)
    source = get_scenario(session, clone_from) if clone_from is not None else None
    scenario = Scenario(
        name=clean,
        notes=note,
        drawdown_order=source.drawdown_order if source is not None else None,
        created_at=datetime.now(UTC).replace(tzinfo=None),
    )
    session.add(scenario)
    session.flush()
    copied = layer_rows(session, source.id) if source is not None else []
    for row in copied:
        session.add(
            Assumption(
                scenario_id=scenario.id,
                key=row.key,
                scope_type=row.scope_type,
                scope_ref=row.scope_ref,
                value_rate_ppm=row.value_rate_ppm,
                value_int=row.value_int,
                value_minor=row.value_minor,
                value_ccy=row.value_ccy,
            )
        )
    copied_phases = phase_rows(session, source.id) if source is not None else []
    for phase in copied_phases:
        session.add(
            SpendingPhase(
                scenario_id=scenario.id,
                anchor=phase.anchor,
                offset_years=phase.offset_years,
                amount_minor=phase.amount_minor,
                amount_ccy=phase.amount_ccy,
            )
        )
    session.flush()
    after = _scenario_snapshot(scenario)
    if source is not None:
        after["copied_from"] = source.name
        after["overrides"] = len(copied)
        after["spending_phases"] = len(copied_phases)
    audit.record(
        session,
        context,
        entity_type="scenario",
        entity_id=scenario.id,
        action="create",
        after=after,
        summary=f"Added Scenario '{clean}'"
        + (f", copied from '{source.name}'" if source is not None else ""),
    )
    return scenario


def update_scenario(
    session: Session, context: AuditContext, scenario_id: int, name: str, notes: str | None
) -> Scenario:
    scenario = get_scenario(session, scenario_id)
    clean, note = _clean_scenario(session, name, notes, own_id=scenario_id)
    if (clean, note) != (scenario.name, scenario.notes):
        before = _scenario_snapshot(scenario)
        scenario.name, scenario.notes = clean, note
        session.flush()
        audit.record(
            session,
            context,
            entity_type="scenario",
            entity_id=scenario.id,
            action="update",
            before=before,
            after=_scenario_snapshot(scenario),
            summary=f"Edited Scenario '{clean}'",
        )
    return scenario


def delete_scenario(session: Session, context: AuditContext, scenario_id: int) -> None:
    """Delete a Scenario and its overrides. A seeded Scenario can be edited but not deleted."""
    scenario = get_scenario(session, scenario_id)
    if scenario.key is not None:
        raise ValidationError([FieldError("scenario", f"{scenario.name} is built in and stays.")])
    saved = session.scalar(
        select(func.count())
        .select_from(ProjectionRun)
        .where(ProjectionRun.scenario_id == scenario_id)
    )
    if saved:
        raise ValidationError(
            [
                FieldError(
                    "scenario",
                    f"{scenario.name} has {saved} saved projection "
                    f"{'run' if saved == 1 else 'runs'}, so it can't be deleted.",
                )
            ]
        )
    rows = layer_rows(session, scenario_id)
    phases = phase_rows(session, scenario_id)
    before = _scenario_snapshot(scenario) | {
        "spending_phases": [_phase_snapshot(p) for p in phases],
        "overrides": [
            {
                "key": r.key,
                "scope_type": r.scope_type,
                "scope_ref": r.scope_ref,
                "value": _audit_value(_value_of(r)),
            }
            for r in rows
        ],
    }
    for row in [*rows, *phases]:
        session.delete(row)
    session.flush()
    session.delete(scenario)
    session.flush()
    audit.record(
        session,
        context,
        entity_type="scenario",
        entity_id=scenario_id,
        action="delete",
        before=before,
        summary=f"Deleted Scenario '{scenario.name}' and its {len(rows)} overrides",
    )


def set_drawdown_order(
    session: Session, context: AuditContext, scenario_id: int, order: Sequence[str] | None
) -> Scenario:
    """Set a Scenario's whole Drawdown policy, or (None) go back to the default order.

    The order overrides as a whole list, never element by element (ADR-0016 s3). The projection
    needs every projected Category to have a place in it, so a valid order names each one once.
    """
    scenario = get_scenario(session, scenario_id)
    if order is not None:
        errors: list[FieldError] = []
        seen: set[str] = set()
        for key in order:
            if key not in assumptions.PROJECTED_CATEGORIES:
                errors.append(FieldError("drawdown_order", f"'{key}' is not a projected Category."))
            elif key in seen:
                errors.append(FieldError("drawdown_order", f"'{key}' appears more than once."))
            seen.add(key)
        if not errors:
            missing = [k for k in assumptions.PROJECTED_CATEGORIES if k not in seen]
            if "cash" in missing:
                # Income streams credit Cash (ADR-0017), and an inflow needs a line in the order.
                errors.append(FieldError("drawdown_order", "Cash must stay in the drawdown order."))
            elif missing:
                errors.append(
                    FieldError(
                        "drawdown_order",
                        "Put every projected Category in the order. Missing: "
                        + ", ".join(missing)
                        + ".",
                    )
                )
        if errors:
            raise ValidationError(errors)
    text = json.dumps(list(order)) if order is not None else None
    if text != scenario.drawdown_order:
        before = _scenario_snapshot(scenario)
        was = drawdown_order_of(scenario)
        scenario.drawdown_order = text
        session.flush()
        audit.record(
            session,
            context,
            entity_type="scenario",
            entity_id=scenario.id,
            action="update",
            before=before,
            after=_scenario_snapshot(scenario),
            summary=f"Changed the Drawdown policy of '{scenario.name}' from {_order_text(was)} "
            f"to {_order_text(order)}",
        )
    return scenario


# --- Assumption writes ---------------------------------------------------------------------


def _check_scope(session: Session, change: Change, member_ids: set[int]) -> str | None:
    """A reason the key cannot be stored at this scope, or None."""
    spec = assumptions.spec_of(change.key)
    if spec is None:
        return f"Unknown Assumption '{change.key}'."
    if change.scope_type not in spec.scopes:
        return f"{spec.label} cannot be set at {change.scope_type} scope."
    if change.scope_type == assumptions.CATEGORY and (
        change.scope_ref not in assumptions.PROJECTED_CATEGORIES
    ):
        return "This Category is not projected."
    if change.scope_type == assumptions.MEMBER and not (
        change.scope_ref.isdecimal() and int(change.scope_ref) in member_ids
    ):
        return "This Member does not exist."
    if change.scope_type == assumptions.HOUSEHOLD and change.scope_ref != "":
        return "A household-wide value has no scope reference."
    return None


def set_assumptions(
    session: Session, context: AuditContext, scenario_id: int | None, changes: Sequence[Change]
) -> list[Review]:
    """Apply edits to one layer (None is the Household defaults) in one transaction.

    Every problem is collected against its field (``key`` or ``key.scope_ref``) and raised
    together; nothing is written then. Returns, for each Household default changed, the Scenarios
    that override it and so did not move (ADR-0016 s6), so the caller can ask the user to review.
    """
    scenario = get_scenario(session, scenario_id) if scenario_id is not None else None
    layer = scenario.name if scenario is not None else "Household default"
    member_ids = {m.id for m in household.list_members(session)}
    parsed: list[tuple[Change, Value | None]] = []
    errors: list[FieldError] = []
    for change in changes:
        field = f"{change.key}.{change.scope_ref}" if change.scope_ref else change.key
        problem = _check_scope(session, change, member_ids)
        if problem is not None:
            errors.append(FieldError(field, problem))
            continue
        if change.value is None or not change.value.strip():
            parsed.append((change, None))
            continue
        try:
            parsed.append((change, assumptions.parse_value(change.key, change.value)))
        except assumptions.AssumptionValueError as error:
            errors.append(FieldError(field, str(error)))
    if errors:
        raise ValidationError(errors)

    existing = {(r.key, r.scope_type, r.scope_ref): r for r in layer_rows(session, scenario_id)}
    reviews: list[Review] = []
    for change, value in parsed:
        slot = (change.key, change.scope_type, change.scope_ref)
        row = existing.get(slot)
        label = _label(change.key, change.scope_ref)
        snap = {"key": change.key, "scope_type": change.scope_type, "scope_ref": change.scope_ref}
        snap_layer = {"scenario": scenario.name if scenario is not None else None}
        if value is None:
            if row is None:
                continue
            old = _value_of(row)
            session.delete(row)
            session.flush()
            audit.record(
                session,
                context,
                entity_type="assumption",
                entity_id=row.id,
                action="delete",
                before=snap_layer | snap | {"value": _audit_value(old)},
                summary=f"Removed {label} from {layer}"
                + (": it inherits again" if scenario is not None else ""),
            )
        elif row is None:
            row = Assumption(scenario_id=scenario_id, **_slot(change))
            store_value(row, value)
            session.add(row)
            session.flush()
            audit.record(
                session,
                context,
                entity_type="assumption",
                entity_id=row.id,
                action="create",
                after=snap_layer | snap | {"value": _audit_value(value)},
                summary=f"Set {label} to {assumptions.format_value(value)} in {layer}",
            )
        else:
            old = _value_of(row)
            if old == value:
                continue
            store_value(row, value)
            session.flush()
            audit.record(
                session,
                context,
                entity_type="assumption",
                entity_id=row.id,
                action="update",
                before=snap_layer | snap | {"value": _audit_value(old)},
                after=snap_layer | snap | {"value": _audit_value(value)},
                summary=f"Changed {label} in {layer} from {assumptions.format_value(old)} "
                f"to {assumptions.format_value(value)}",
            )
        if scenario is None:
            names = overriding_scenarios(session, *slot)
            if names:
                reviews.append(Review(*slot, names))
    return reviews


def _slot(change: Change) -> dict[str, str]:
    return {"key": change.key, "scope_type": change.scope_type, "scope_ref": change.scope_ref}


# --- spending phase writes ----------------------------------------------------------------


def _phase_text(anchor: str, offset_years: int) -> str:
    """For audit summaries: "from the last retirement + 10 years"."""
    if offset_years == 0:
        return f"from {spending.ANCHOR_LABEL[anchor]}"
    unit = "year" if offset_years == 1 else "years"
    return f"from {spending.ANCHOR_LABEL[anchor]} + {offset_years} {unit}"


def set_spending_phases(
    session: Session,
    context: AuditContext,
    scenario_id: int | None,
    entries: Sequence[tuple[str, int, str]] | None,
) -> list[Review]:
    """Replace one layer's whole spending phase list (None is the Household default layer).

    ``entries`` are ``(anchor, offset_years, amount text)``. A Scenario's list replaces the
    Household default list as a whole, so a Scenario given None or an empty list goes back to
    inheriting; for the Household default an empty list leaves spending unset. Every problem is
    collected against its field (``spending_phases.<i>.<field>``) and raised together; nothing is
    written then. Phases are matched to the stored rows by ``(anchor, offset_years)`` and each
    add, change and removal is its own audit record. Returns, when the Household default list
    changed, the Scenarios that state their own list and so did not move (ADR-0016 section 6), so
    the caller can ask for a review.
    """
    scenario = get_scenario(session, scenario_id) if scenario_id is not None else None
    layer = scenario.name if scenario is not None else "Household default"
    wanted, errors = spending.parse(entries or [])
    if errors:
        raise ValidationError(errors)

    existing = {(r.anchor, r.offset_years): r for r in phase_rows(session, scenario_id)}
    snap_layer = {"scenario": scenario.name if scenario is not None else None}
    changed = False
    keep: set[tuple[str, int]] = set()
    for phase in wanted:
        slot = (phase.anchor, phase.offset_years)
        keep.add(slot)
        amount = phase.annual_amount
        row = existing.get(slot)
        text = f"{format_amount(amount)} a year {_phase_text(*slot)}"
        if row is None:
            row = SpendingPhase(
                scenario_id=scenario_id,
                anchor=phase.anchor,
                offset_years=phase.offset_years,
                amount_minor=amount.amount_minor,
                amount_ccy=amount.currency,
            )
            session.add(row)
            session.flush()
            audit.record(
                session,
                context,
                entity_type="spending_phase",
                entity_id=row.id,
                action="create",
                after=snap_layer | _phase_snapshot(row),
                summary=f"Added a spending phase to {layer}: {text}",
            )
            changed = True
        elif _phase_of(row).annual_amount != amount:
            before = snap_layer | _phase_snapshot(row)
            was = format_amount(_phase_of(row).annual_amount)
            row.amount_minor, row.amount_ccy = amount.amount_minor, amount.currency
            session.flush()
            audit.record(
                session,
                context,
                entity_type="spending_phase",
                entity_id=row.id,
                action="update",
                before=before,
                after=snap_layer | _phase_snapshot(row),
                summary=f"Changed a spending phase in {layer} {_phase_text(*slot)} "
                f"from {was} to {format_amount(amount)} a year",
            )
            changed = True
    for slot, row in existing.items():
        if slot in keep:
            continue
        before = snap_layer | _phase_snapshot(row)
        session.delete(row)
        session.flush()
        audit.record(
            session,
            context,
            entity_type="spending_phase",
            entity_id=row.id,
            action="delete",
            before=before,
            summary=f"Removed a spending phase from {layer} {_phase_text(*slot)}"
            + (": it inherits again" if scenario is not None and not wanted else ""),
        )
        changed = True
    if scenario is not None or not changed:
        return []
    names = tuple(scenarios_with_own_spending(session).values())
    return [Review(spending.FIELD, assumptions.HOUSEHOLD, "", names)] if names else []


# --- fixture -------------------------------------------------------------------------------

FIXTURE_CONTEXT = AuditContext("system", "fixture", "fixture")


def _changes(entries: Sequence[dict[str, str]], member_ids: dict[str, int]) -> list[Change]:
    out: list[Change] = []
    for e in entries:
        ref = e.get("scope_ref", "")
        if e["scope_type"] == assumptions.MEMBER:
            ref = str(member_ids[ref])  # the fixture names a Member by name
        out.append(Change(e["key"], e["scope_type"], ref, e["value"]))
    return out


def _phase_entries(entry: dict[str, Any]) -> list[tuple[str, int, str]]:
    return [
        (p["anchor"], p["offset_years"], p["annual_amount"])
        for p in entry.get("spending_phases", [])
    ]


def load_fixture_assumptions(session: Session, data_dir: Path) -> int:
    """Set the fixture's Household defaults and custom Scenarios, once, while there are none.

    Reads ``assumptions`` from ``<data_dir>/household.json``: ``defaults`` (rows layered over the
    seeded ones) and ``scenarios`` (a name, notes, ``overrides`` and an optional
    ``drawdown_order`` and its own ``spending_phases``). Members are named by name. The top-level
    ``spending_phases`` are the Household default list, each ``{anchor, offset_years,
    annual_amount}``.
    Skipped once any Household default spending phase exists or any custom Scenario does.
    """
    path = data_dir / household.FIXTURE_FILENAME
    if not path.is_file():
        return 0
    entry = json.loads(path.read_text(encoding="utf-8")).get("assumptions")
    spending_set = session.scalar(
        select(func.count()).select_from(SpendingPhase).where(SpendingPhase.scenario_id.is_(None))
    )
    custom = session.scalar(
        select(func.count()).select_from(Scenario).where(Scenario.key.is_(None))
    )
    if not entry or spending_set or custom:
        return 0
    ids = {m.name: m.id for m in household.list_members(session)}
    set_assumptions(session, FIXTURE_CONTEXT, None, _changes(entry.get("defaults", []), ids))
    set_spending_phases(session, FIXTURE_CONTEXT, None, _phase_entries(entry))
    for item in entry.get("scenarios", []):
        scenario = create_scenario(session, FIXTURE_CONTEXT, item["name"], item.get("notes"))
        if item.get("drawdown_order") is not None:
            set_drawdown_order(session, FIXTURE_CONTEXT, scenario.id, item["drawdown_order"])
        set_assumptions(session, FIXTURE_CONTEXT, scenario.id, _changes(item["overrides"], ids))
        if item.get("spending_phases"):
            set_spending_phases(session, FIXTURE_CONTEXT, scenario.id, _phase_entries(item))
    return len(entry.get("scenarios", []))
