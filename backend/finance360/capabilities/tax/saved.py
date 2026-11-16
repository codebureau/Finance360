"""Tax what-ifs and saved Tax estimates (ADR-0019 section 6, following ADR-0016).

A **Tax what-if** is editable and audited (entity type ``tax_whatif``): a name, a financial year and
a list of overrides (``whatif.py``). It is a base plus overrides, never inheriting from another.

A **saved Tax estimate** is the immutable counterpart of ADR-0016's saved run: written once, on an
explicit save, and never changed (triggers reject UPDATE and DELETE; audited as ``create`` only).
It **pins** everything that explains it: ``input_json`` holds the Label totals and the full
``TaxRules`` block, ``result_json`` the result, and ``provenance_json`` each Contribution, each
override applied and the Member names. So reading one never touches the register, the Rule pack,
the Household or the engine. Reproduction is ``engine.estimate(from_json(input_json))``, equal to
the stored result whenever the engine version is current; otherwise the stored result is shown as
computed by that version, and "re-run with the current engine" saves a **new** estimate.

Every function takes and returns structured values, so the API and a later MCP or AI layer can call
it. ``origin`` is ``user`` or ``ai``: an AI may turn a question into overrides, which are validated
here deterministically, and may calculate a what-if it marks ``ai``; it never writes Tax records.
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
from finance360.capabilities.tax import (
    compare,
    engine,
    labels,
    prefill,
    result_json,
    service,
    whatif,
)
from finance360.capabilities.tax import input as tinput
from finance360.core import audit
from finance360.core.audit import AuditContext
from finance360.core.errors import FieldError, NotFoundError, ValidationError
from finance360.core.models.tax_estimate import (
    LABEL_MAX,
    NAME_MAX,
    NOTES_MAX,
    ORIGINS,
    TaxEstimate,
    TaxWhatIf,
)
from finance360.core.money import BASE_CURRENCY, MoneyError, parse_amount
from finance360.core.rulepack import fy_label

#: Estimates are for the whole Household, which is at most two Members (``TaxInput``).
MAX_MEMBERS = tinput.MAX_MEMBERS


# --- Tax what-ifs ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OverrideInput:
    """One override as the household states it. Amounts are decimal text in AUD (never a float).

    ``set``: ``member_id``, ``label`` and the value for the label's kind (``amount``, ``days`` or
    ``flag``). ``exclude_label``: ``member_id`` and ``label``. ``exclude_record``: ``record_id``.
    ``add_record``: ``record``, a Tax record as the register takes it.
    """

    op: str
    member_id: int | None = None
    label: str | None = None
    amount: str | None = None
    days: int | None = None
    flag: bool | None = None
    record_id: int | None = None
    record: service.RecordInput | None = None


@dataclass(frozen=True)
class WhatIfInput:
    name: str
    financial_year: int
    overrides: Sequence[OverrideInput] = ()
    notes: str | None = None


@dataclass(frozen=True)
class WhatIfView:
    id: int
    name: str
    financial_year: int
    notes: str | None
    origin: str
    created_at: datetime
    overrides: tuple[whatif.Override, ...]
    #: Saved estimates made from it (a what-if with any cannot be deleted).
    estimate_count: int


def _payload_of(item: OverrideInput, label: labels.Label | None) -> dict[str, Any]:
    """The override as ``whatif`` parses it. A value that is not a number the label takes is left
    out, so the parser reports it against the override."""
    body: dict[str, Any] = {"op": item.op}
    if item.member_id is not None:
        body["member_id"] = item.member_id
    if item.label is not None:
        body["label"] = item.label
    if item.record_id is not None:
        body["record_id"] = item.record_id
    if item.record is not None:
        body["record"] = service.record_input_to_json(item.record)
    if item.op == whatif.SET and label is not None:
        if label.kind == labels.MONEY:
            if item.amount is None or not item.amount.strip():
                raise whatif.WhatIfError("Enter an amount.")
            try:
                money = parse_amount(item.amount, BASE_CURRENCY)
            except MoneyError as error:
                raise whatif.WhatIfError(str(error)) from None
            body["value"] = tinput.money_out(money)
        elif label.kind == labels.DAYS:
            body["value"] = item.days
        else:
            body["value"] = item.flag
    return body


def _convert(items: Sequence[OverrideInput], fy: int) -> tuple[whatif.Override, ...]:
    errors: list[FieldError] = []
    parsed: list[whatif.Override] = []
    for index, item in enumerate(items):
        try:
            parsed.extend(
                whatif.from_payload([_payload_of(item, labels.get(item.label or ""))], fy)
            )
        except whatif.WhatIfError as error:
            errors.append(FieldError(f"overrides[{index}]", str(error)))
    if not errors:
        try:
            whatif.check(parsed)
        except whatif.WhatIfError as error:
            field = "overrides" if error.index is None else f"overrides[{error.index}]"
            errors.append(FieldError(field, str(error)))
    if errors:
        raise ValidationError(errors)
    return tuple(parsed)


def _view(session: Session, row: TaxWhatIf) -> WhatIfView:
    count = session.scalar(
        select(func.count()).select_from(TaxEstimate).where(TaxEstimate.whatif_id == row.id)
    )
    return WhatIfView(
        row.id,
        row.name,
        row.financial_year,
        row.notes,
        row.origin,
        row.created_at,
        whatif.from_json(row.overrides_json),
        count or 0,
    )


def _snapshot(row: TaxWhatIf) -> dict[str, Any]:
    return {
        "name": row.name,
        "financial_year": row.financial_year,
        "notes": row.notes,
        "origin": row.origin,
        "overrides": whatif.to_payload(whatif.from_json(row.overrides_json)),
    }


def _checked(
    session: Session, given: WhatIfInput, origin: str, own_id: int | None
) -> tuple[str, str | None, tuple[whatif.Override, ...]]:
    errors: list[FieldError] = []
    name = given.name.strip()
    if not name:
        errors.append(FieldError("name", "Name is required."))
    elif len(name) > NAME_MAX:
        errors.append(FieldError("name", f"Name must be at most {NAME_MAX} characters."))
    elif session.scalar(
        select(TaxWhatIf.id).where(TaxWhatIf.name == name, TaxWhatIf.id != (own_id or 0))
    ):
        errors.append(FieldError("name", "Another Tax what-if has this name."))
    notes = (given.notes or "").strip() or None
    if notes is not None and len(notes) > NOTES_MAX:
        errors.append(FieldError("notes", f"Notes must be at most {NOTES_MAX} characters."))
    if not service.EARLIEST_FY <= given.financial_year <= service.LATEST_FY:
        errors.append(
            FieldError(
                "financial_year",
                f"Enter a year from {service.EARLIEST_FY} to {service.LATEST_FY}.",
            )
        )
    if origin not in ORIGINS:
        errors.append(FieldError("origin", f"Origin must be one of: {', '.join(ORIGINS)}."))
    overrides: tuple[whatif.Override, ...] = ()
    try:
        overrides = _convert(given.overrides, given.financial_year)
    except ValidationError as error:
        errors.extend(error.errors)
    else:
        errors.extend(service.check_overrides(session, overrides))
    if errors:
        raise ValidationError(errors)
    return name, notes, overrides


def list_whatifs(session: Session, financial_year: int | None = None) -> list[WhatIfView]:
    statement = select(TaxWhatIf)
    if financial_year is not None:
        statement = statement.where(TaxWhatIf.financial_year == financial_year)
    rows = session.scalars(statement.order_by(TaxWhatIf.financial_year.desc(), TaxWhatIf.name))
    return [_view(session, row) for row in rows]


def _whatif_row(session: Session, whatif_id: int) -> TaxWhatIf:
    row = session.get(TaxWhatIf, whatif_id)
    if row is None:
        raise NotFoundError(f"Tax what-if {whatif_id} does not exist.")
    return row


def get_whatif(session: Session, whatif_id: int) -> WhatIfView:
    return _view(session, _whatif_row(session, whatif_id))


def create_whatif(
    session: Session, context: AuditContext, given: WhatIfInput, origin: str = "user"
) -> WhatIfView:
    name, notes, overrides = _checked(session, given, origin, None)
    row = TaxWhatIf(
        name=name,
        financial_year=given.financial_year,
        notes=notes,
        origin=origin,
        overrides_json=whatif.to_json(overrides),
        created_at=datetime.now(UTC).replace(tzinfo=None),
    )
    session.add(row)
    session.flush()
    audit.record(
        session,
        context,
        entity_type="tax_whatif",
        entity_id=row.id,
        action="create",
        after=_snapshot(row),
        summary=f"Added Tax what-if '{row.name}' for FY {fy_label(row.financial_year)}",
    )
    return _view(session, row)


def update_whatif(
    session: Session, context: AuditContext, whatif_id: int, given: WhatIfInput
) -> WhatIfView:
    """Replace a what-if's name, year, notes and overrides (as a whole list). Saved estimates made
    from it are unchanged: they hold their own pinned copy."""
    row = _whatif_row(session, whatif_id)
    name, notes, overrides = _checked(session, given, row.origin, whatif_id)
    before = _snapshot(row)
    row.name = name
    row.notes = notes
    row.financial_year = given.financial_year
    row.overrides_json = whatif.to_json(overrides)
    session.flush()
    after = _snapshot(row)
    if after != before:
        audit.record(
            session,
            context,
            entity_type="tax_whatif",
            entity_id=row.id,
            action="update",
            before=before,
            after=after,
            summary=f"Edited Tax what-if '{row.name}'",
        )
    return _view(session, row)


def delete_whatif(session: Session, context: AuditContext, whatif_id: int) -> None:
    """Delete a what-if that has no saved estimates (the estimates keep it: RESTRICT)."""
    row = _whatif_row(session, whatif_id)
    saved = _view(session, row).estimate_count
    if saved:
        raise ValidationError(
            [
                FieldError(
                    "whatif",
                    f"'{row.name}' has {saved} saved "
                    f"{'estimate' if saved == 1 else 'estimates'}, so it can't be deleted.",
                )
            ]
        )
    before, name = _snapshot(row), row.name
    session.delete(row)
    session.flush()
    audit.record(
        session,
        context,
        entity_type="tax_whatif",
        entity_id=whatif_id,
        action="delete",
        before=before,
        summary=f"Deleted Tax what-if '{name}'",
    )


# --- calculating and saving ----------------------------------------------------------------


@dataclass(frozen=True)
class Body:
    """What an estimate shows: its pinned input, its result and its provenance. The same shape
    whether it was just calculated or read back from a saved row."""

    input: tinput.TaxInput
    result: engine.TaxResult
    provenance: dict[str, Any]

    def member_name(self, member_id: int) -> str:
        for member in self.provenance["members"]:
            if member["member_id"] == member_id:
                return str(member["name"])
        return f"Member {member_id}"


@dataclass(frozen=True)
class EstimateHeader:
    """What a list shows: identity and flags, never a payload."""

    id: int
    created_at: datetime
    label: str | None
    financial_year: int
    whatif_id: int | None
    whatif_name: str | None
    engine_version: int
    input_version: int
    rule_pack_version: str
    incomplete: bool
    has_provisional: bool

    @property
    def current_engine(self) -> bool:
        return self.engine_version == engine.TAX_ENGINE_VERSION


@dataclass(frozen=True)
class SavedEstimate:
    """A saved estimate rebuilt from its snapshot alone."""

    header: EstimateHeader
    body: Body
    #: Re-running the pinned input with the engine of the day equals the stored result. ``None``
    #: when the estimate was computed by an older engine (so a re-run would differ by design).
    reproduces: bool | None


#: A prefill status only a saved estimate's provenance carries: a what-if replaced the total.
OVERRIDDEN = "overridden"


def _provenance(
    built: service.Built, whatif_id: int | None, whatif_name: str | None
) -> dict[str, Any]:
    stated = {(e["member_id"], e["label"]) for e in built.effects if e["op"] == whatif.SET}
    #: A prefilled total a what-if replaced or left out no longer stands, so its lines say so.
    replaced = {
        (e["member_id"], e["label"])
        for e in built.effects
        if e["op"] in (whatif.SET, whatif.EXCLUDE_LABEL)
    }
    counted = {
        (line.member_id, line.label) for line in built.prefill if line.status == prefill.COUNTED
    }
    return {
        "version": 1,
        "members": [{"member_id": m, "name": n} for m, n in sorted(built.member_names.items())],
        "whatif": None if whatif_id is None else {"id": whatif_id, "name": whatif_name},
        "totals": [
            {
                "member_id": t.member_id,
                "label": t.label,
                "overridden": (t.member_id, t.label) in stated,
                # Where the figure came from: the register's records, an Income stream's
                # forecast (prefill), or a total a what-if stated.
                "source": (
                    "stated"
                    if (t.member_id, t.label) in stated
                    else "prefill"
                    if (t.member_id, t.label) in counted
                    else "records"
                ),
                "value": service.out_value(t.total),
                "contributions": [
                    {
                        "record_id": c.record_id,
                        "description": built.descriptions.get(c.record_id, ""),
                        "value": service.out_value(c.value),
                    }
                    for c in t.contributions
                ],
            }
            for t in built.totals
        ],
        "overrides": list(built.effects),
        # Every Income stream line considered for the base, counted or not, and why.
        "prefill": [
            {
                "member_id": line.member_id,
                "label": line.label,
                "stream_id": line.stream_id,
                "stream_name": line.stream_name,
                "value": service.out_value(line.amount),
                "status": (
                    OVERRIDDEN
                    if line.status == prefill.COUNTED and (line.member_id, line.label) in replaced
                    else line.status
                ),
                "text": line.text,
            }
            for line in built.prefill
        ],
    }


def _prepared(
    session: Session,
    *,
    whatif_id: int | None,
    financial_year: int,
    dependants: int,
    member_ids: Sequence[int],
) -> tuple[service.Built, TaxWhatIf | None]:
    row: TaxWhatIf | None = None
    overrides: tuple[whatif.Override, ...] = ()
    if whatif_id is not None:
        row = _whatif_row(session, whatif_id)
        if row.financial_year != financial_year:
            raise ValidationError(
                [
                    FieldError(
                        "financial_year",
                        f"'{row.name}' is a Tax what-if for FY {fy_label(row.financial_year)}.",
                    )
                ]
            )
        overrides = whatif.from_json(row.overrides_json, financial_year)
    built = service.build(
        session,
        financial_year=financial_year,
        dependants=dependants,
        member_ids=member_ids,
        overrides=overrides,
        whatif_name=row.name if row else None,
    )
    return built, row


def calculate(
    session: Session,
    *,
    financial_year: int,
    dependants: int,
    member_ids: Sequence[int],
    whatif_id: int | None = None,
) -> Body:
    """Calculate the base (``whatif_id`` ``None``) or a Tax what-if now. Nothing is saved."""
    built, row = _prepared(
        session,
        whatif_id=whatif_id,
        financial_year=financial_year,
        dependants=dependants,
        member_ids=member_ids,
    )
    return Body(
        built.input,
        built.result,
        _provenance(built, row.id if row else None, row.name if row else None),
    )


def _clean_label(label: str | None) -> str | None:
    clean = (label or "").strip() or None
    if clean is not None and len(clean) > LABEL_MAX:
        raise ValidationError(
            [FieldError("label", f"Label must be at most {LABEL_MAX} characters.")]
        )
    return clean


def _header(row: TaxEstimate) -> EstimateHeader:
    return EstimateHeader(
        row.id,
        row.created_at,
        row.label,
        row.financial_year,
        row.whatif_id,
        row.whatif_name,
        row.tax_engine_version,
        row.tax_input_version,
        row.rule_pack_version,
        row.incomplete,
        row.has_provisional,
    )


def _insert(
    session: Session,
    context: AuditContext,
    *,
    label: str | None,
    body: Body,
    rule_pack_version: str,
    whatif_id: int | None,
    whatif_name: str | None,
    summary: str,
    extra: dict[str, Any],
) -> TaxEstimate:
    row = TaxEstimate(
        created_at=datetime.now(UTC).replace(tzinfo=None),
        label=label,
        financial_year=body.input.financial_year,
        whatif_id=whatif_id,
        whatif_name=whatif_name,
        tax_engine_version=body.result.engine_version,
        tax_input_version=body.input.input_version,
        rule_pack_version=rule_pack_version,
        input_json=tinput.to_json(body.input),
        result_json=result_json.to_json(body.result),
        provenance_json=_dumps(body.provenance),
        incomplete=body.result.incomplete,
        has_provisional=body.result.has_provisional,
    )
    session.add(row)
    session.flush()
    # Header fields only, never the payload (ADR-0016 section 5).
    audit.record(
        session,
        context,
        entity_type="tax_estimate",
        entity_id=row.id,
        action="create",
        after={
            "label": row.label,
            "financial_year": row.financial_year,
            "whatif_id": row.whatif_id,
            "whatif_name": row.whatif_name,
            "tax_engine_version": row.tax_engine_version,
            "tax_input_version": row.tax_input_version,
            "rule_pack_version": row.rule_pack_version,
            "incomplete": row.incomplete,
            "has_provisional": row.has_provisional,
        }
        | extra,
        summary=summary,
    )
    return row


def _dumps(payload: dict[str, Any]) -> str:
    tinput.reject_floats(payload)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def save_estimate(
    session: Session,
    context: AuditContext,
    *,
    financial_year: int,
    dependants: int,
    member_ids: Sequence[int],
    whatif_id: int | None = None,
    label: str | None = None,
) -> EstimateHeader:
    """Calculate the base (``whatif_id`` ``None``) or a Tax what-if now and save it, once."""
    clean = _clean_label(label)
    built, row = _prepared(
        session,
        whatif_id=whatif_id,
        financial_year=financial_year,
        dependants=dependants,
        member_ids=member_ids,
    )
    body = Body(
        built.input,
        built.result,
        _provenance(built, row.id if row else None, row.name if row else None),
    )
    what = f"Tax what-if '{row.name}'" if row else "the base"
    saved = _insert(
        session,
        context,
        label=clean,
        body=body,
        rule_pack_version=built.pack.version_label,
        whatif_id=row.id if row else None,
        whatif_name=row.name if row else None,
        summary=f"Saved a Tax estimate of {what} for FY {fy_label(financial_year)}"
        + (f" ({clean})" if clean else ""),
        extra={},
    )
    return _header(saved)


def rerun_with_current_engine(
    session: Session, context: AuditContext, estimate_id: int
) -> EstimateHeader:
    """Calculate a saved estimate's pinned input with the current engine and save a new estimate.

    The stored estimate is untouched. Nothing live is read: the input is the snapshot, upcast to
    the current input version if it is older.
    """
    old = _row(session, estimate_id)
    if old.tax_engine_version == engine.TAX_ENGINE_VERSION:
        raise ValidationError(
            [FieldError("estimate", "This estimate was computed by the current engine already.")]
        )
    tax_input = tinput.from_json(old.input_json)
    provenance = tinput.as_obj(tinput.loads(old.provenance_json), "provenance")
    body = Body(
        tax_input,
        engine.estimate(tax_input),
        dict(provenance) | {"rerun_of": old.id},
    )
    row = _insert(
        session,
        context,
        label=f"Re-run of estimate {old.id}",
        body=body,
        rule_pack_version=old.rule_pack_version,
        whatif_id=old.whatif_id,
        whatif_name=old.whatif_name,
        summary=f"Re-ran Tax estimate {old.id} with engine v{engine.TAX_ENGINE_VERSION}",
        extra={"rerun_of": old.id},
    )
    return _header(row)


# --- reads ---------------------------------------------------------------------------------


def _row(session: Session, estimate_id: int) -> TaxEstimate:
    row = session.get(TaxEstimate, estimate_id)
    if row is None:
        raise NotFoundError(f"Tax estimate {estimate_id} does not exist.")
    return row


def list_estimates(session: Session, financial_year: int | None = None) -> list[EstimateHeader]:
    """Newest first. Selects the header columns only: no payload is read."""
    columns = (
        TaxEstimate.id,
        TaxEstimate.created_at,
        TaxEstimate.label,
        TaxEstimate.financial_year,
        TaxEstimate.whatif_id,
        TaxEstimate.whatif_name,
        TaxEstimate.tax_engine_version,
        TaxEstimate.tax_input_version,
        TaxEstimate.rule_pack_version,
        TaxEstimate.incomplete,
        TaxEstimate.has_provisional,
    )
    statement = select(*columns)
    if financial_year is not None:
        statement = statement.where(TaxEstimate.financial_year == financial_year)
    return [EstimateHeader(*r) for r in session.execute(statement.order_by(TaxEstimate.id.desc()))]


def _body(row: TaxEstimate) -> Body:
    return Body(
        tinput.from_json(row.input_json),
        result_json.from_json(row.result_json),
        dict(tinput.as_obj(tinput.loads(row.provenance_json), "provenance")),
    )


def get_estimate(session: Session, estimate_id: int) -> SavedEstimate:
    """A saved estimate rebuilt from its stored snapshot alone."""
    row = _row(session, estimate_id)
    body = _body(row)
    reproduces = (
        engine.estimate(body.input) == body.result
        if row.tax_engine_version == engine.TAX_ENGINE_VERSION
        else None
    )
    return SavedEstimate(_header(row), body, reproduces)


@dataclass(frozen=True)
class Compared:
    a: EstimateHeader
    b: EstimateHeader
    names: dict[int, str]
    comparison: compare.Comparison


def compare_estimates(session: Session, a_id: int, b_id: int) -> Compared:
    """The per-label and per-result deltas (``b`` less ``a``) of two saved estimates of one year."""
    if a_id == b_id:
        raise ValidationError([FieldError("b", "Choose two different estimates.")])
    first, second = _row(session, a_id), _row(session, b_id)
    one, two = _body(first), _body(second)
    try:
        comparison = compare.compare(
            compare.Side(one.input, one.result), compare.Side(two.input, two.result)
        )
    except compare.CompareError as error:
        raise ValidationError([FieldError("b", str(error))]) from None
    names = {m: one.member_name(m) for m in comparison.member_ids}
    return Compared(_header(first), _header(second), names, comparison)


# --- fixture -------------------------------------------------------------------------------

FIXTURE_FILENAME = "tax_whatifs.json"


def load_fixture_tax_whatifs(session: Session, data_dir: Path) -> int:
    """Create the fixture's Tax what-ifs and saved estimates through the service, once, while
    there are none.

    Reads ``<data_dir>/tax_whatifs.json`` (a file of its own, so the household file's counts do not
    move): ``what_ifs``, each with a ``name``, ``financial_year``, optional ``notes`` and
    ``overrides`` naming a Member by ``member``, and ``estimates``, each a ``label``,
    ``financial_year`` and ``dependants`` and the ``what_if`` it is of (a name, or none for the
    base). The estimates are calculated with the current engine, so none is stale. Needs the Tax
    records (run after ``load_fixture_tax_records``).
    """
    path = data_dir / FIXTURE_FILENAME
    if not path.is_file():
        return 0
    if session.scalar(select(func.count()).select_from(TaxWhatIf)) or session.scalar(
        select(func.count()).select_from(TaxEstimate)
    ):
        return 0
    data = json.loads(path.read_text(encoding="utf-8"))
    members = household.list_members(session)
    ids = {m.name: m.id for m in members}
    records = {
        (v.row.member_id, v.row.description): v.row.id for v in service.list_records(session)
    }
    made: dict[str, int] = {}
    for entry in data.get("what_ifs", []):
        overrides: list[OverrideInput] = []
        for o in entry.get("overrides", []):
            member_id = ids[o["member"]] if "member" in o else None
            if o["op"] == whatif.EXCLUDE_RECORD:
                overrides.append(
                    OverrideInput(
                        whatif.EXCLUDE_RECORD, record_id=records[(member_id, o["record"])]
                    )
                )
            elif o["op"] == whatif.ADD_RECORD:
                given = service.record_input_from_json(dict(o["record"]) | {"member_id": member_id})
                overrides.append(OverrideInput(whatif.ADD_RECORD, record=given))
            else:
                overrides.append(
                    OverrideInput(
                        o["op"],
                        member_id=member_id,
                        label=o.get("label"),
                        amount=o.get("amount"),
                        days=o.get("days"),
                        flag=o.get("flag"),
                    )
                )
        view = create_whatif(
            session,
            service.FIXTURE_CONTEXT,
            WhatIfInput(entry["name"], entry["financial_year"], overrides, entry.get("notes")),
        )
        made[view.name] = view.id
    count = len(made)
    for entry in data.get("estimates", []):
        save_estimate(
            session,
            service.FIXTURE_CONTEXT,
            financial_year=entry["financial_year"],
            dependants=entry.get("dependants", 0),
            member_ids=[m.id for m in members[:MAX_MEMBERS]],
            whatif_id=made[entry["what_if"]] if entry.get("what_if") else None,
            label=entry.get("label"),
        )
        count += 1
    return count
