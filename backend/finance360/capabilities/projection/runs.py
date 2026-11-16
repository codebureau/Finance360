"""Saved projection runs (ADR-0016 sections 4 and 5).

A run is written once, from a Scenario resolved and projected right now, and never changed. It
stores the resolved Projection input, the result and the sustainable-spending output as canonical
JSON (``result_json``), plus the resolver's provenance and the display context the screen needs
(Category and Member names, the flagged Accounts), so reopening it never reads live Assumptions,
the Position, prices or even Member names.

Reads: the list uses the denormalised headline columns only and never loads a payload; the detail
rebuilds a ``service.ProjectionRun`` from the snapshot, which the API renders as it renders a live
projection. Re-running with the current engine projects the stored input again and saves a new run.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from finance360.capabilities.projection import (
    assumptions,
    engine,
    income,
    life_events,
    result_json,
    rules,
    service,
    spending,
    tax_income,
)
from finance360.capabilities.projection import input as pinput
from finance360.capabilities.projection.sustainable import SustainableSpending, sustainable_spending
from finance360.core import audit
from finance360.core.audit import AuditContext
from finance360.core.errors import FieldError, NotFoundError, ValidationError
from finance360.core.models.projection_run import LABEL_MAX
from finance360.core.models.projection_run import ProjectionRun as RunRow
from finance360.core.money import Money, MoneyError
from finance360.core.rate import Rate
from finance360.core.rulepack import Age


@dataclass(frozen=True)
class RunHeader:
    """What a list shows: identity and the headline, never a payload."""

    id: int
    created_at: datetime
    label: str | None
    scenario_id: int
    scenario_name: str
    engine_version: int
    funds_last_to_age: int | None
    lasts_beyond_horizon: bool
    #: ``au/2026-27/r1``; None for a run saved before Rule packs were recorded (B10).
    rule_pack_version: str | None = None

    @property
    def current_engine(self) -> bool:
        return self.engine_version == engine.ENGINE_VERSION


@dataclass(frozen=True)
class SavedRun:
    """A run rebuilt from its snapshot alone."""

    header: RunHeader
    run: service.ProjectionRun
    provenance: tuple[assumptions.Source, ...]
    #: Which layer supplied the spending phases; None for a run saved before spending phases (B8).
    spending_layer: str | None = None


# --- provenance snapshot -------------------------------------------------------------------


def _value_out(value: assumptions.Value) -> Any:
    """ADR-0016 s5: ``{minor, ccy}`` for Money, a percent string for a Rate, an int."""
    if isinstance(value, Money):
        return {"ccy": value.currency, "minor": value.amount_minor}
    if isinstance(value, Rate):
        return value.to_percent()
    return value


#: Keys that were Assumptions when a run was saved and have since been retired, with their kind, so
#: a stored run still reads. ``annual_spending`` became spending phases (B8).
RETIRED_KEYS = {"annual_spending": "money"}


def _value_in(key: str, raw: Any) -> assumptions.Value:
    kind = assumptions.KEYS[key].kind if key in assumptions.KEYS else RETIRED_KEYS[key]
    if kind == "money":
        return Money(raw["minor"], raw["ccy"])
    if kind == "rate":
        return Rate.from_percent(raw)
    if kind == "choice":
        return str(raw)
    return int(raw)


def _provenance_payload(
    prepared: service.Prepared, sources: Sequence[assumptions.Source], spending_layer: str | None
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "assumptions": [
            {
                "key": s.key,
                "layer": s.layer,
                "scope_ref": s.scope_ref,
                "scope_type": s.scope_type,
                "value": _value_out(s.value),
            }
            for s in sources
        ],
        "categories": [{"key": k, "name": n} for k, n in prepared.categories],
        "first_retirement_year": prepared.first_retirement_year,
        "flagged": [
            {
                "account_id": f.account_id,
                "category_key": f.category_key,
                "name": f.name,
                "status": f.status,
            }
            for f in prepared.flagged
        ],
        "last_retirement_year": prepared.last_retirement_year,
        "members": [
            {
                "age_now": m.age_now,
                "member_id": m.member_id,
                "name": m.name,
                "retirement_year": m.retirement_year,
            }
            for m in prepared.members
        ],
    }
    if prepared.spending is not None and spending_layer is not None:  # absent before B8
        payload["spending"] = {
            "layer": spending_layer,
            "phases": [
                {
                    "anchor": p.anchor,
                    "annual_amount": _value_out(p.annual_amount),
                    "offset_years": p.offset_years,
                }
                for p in prepared.spending
            ],
        }
    if prepared.income is not None:  # absent in runs saved before B7
        payload["income"] = _income_payload(prepared.income)
    if prepared.tax is not None:  # absent when tax is off, and in runs saved before it
        payload["tax"] = _tax_payload(prepared.tax)
    if prepared.debits is not None:  # absent in runs saved by engine 1 (ADR-0028 s4)
        payload["debits"] = _debits_payload(prepared.debits)
    if prepared.rules is not None:  # absent in runs saved before B10
        payload["rules"] = _rules_payload(prepared.rules)
    if prepared.life_events is not None:  # absent in runs saved before B6
        payload["life_events"] = [
            {
                "age": str(e.age),
                "kind": e.kind,
                "member_id": e.member_id,
                "on": e.on.isoformat(),
                "source": e.source,
                "year": e.year,
            }
            for e in prepared.life_events
        ]
    return payload


def _income_payload(used: income.IncomeUsed) -> dict[str, Any]:
    """ADR-0017 s7: each stream used as stored (and the years and growth applied) and each Rule
    pack rate used, so editing or deleting a stream, or a pack update, never changes a saved run."""
    return {
        "rates": [
            {
                "fy": r.fy,
                "from_year": r.from_year,
                "key": r.key,
                "layer": r.layer,
                "source": r.source,
                "value": r.value.to_percent(),
            }
            for r in used.rates
        ],
        "streams": [
            {
                "credit_category": u.stream.credit_category,
                "end_event": u.stream.end_event,
                "end_on": u.stream.end_on.isoformat() if u.stream.end_on else None,
                "from_year": u.span.from_year,
                "gross_annual": _value_out(u.stream.gross_annual),
                "id": u.stream.id,
                "member_id": u.stream.member_id,
                "name": u.stream.name,
                "real_wage_growth": u.growth.to_percent(),
                "start_event": u.stream.start_event,
                "start_on": u.stream.start_on.isoformat() if u.stream.start_on else None,
                "take_home_annual": _value_out(u.stream.take_home_annual),
                "type_key": u.stream.type_key,
                "until_year": u.span.until_year,
            }
            for u in used.streams
        ],
    }


def _tax_payload(used: tax_income.TaxUsed) -> dict[str, Any]:
    """ADR-0019 s1: the tax engine and Rule pack versions, each year's figures and each Division
    293 payment, kept with the run so a later engine or pack change never alters it."""
    return {
        "div293_unavailable": used.div293_unavailable,
        "engine_version": used.tax_engine_version,
        "input_version": used.tax_input_version,
        "payers": [
            {"layer": p.layer, "member_id": p.member_id, "payer": p.payer} for p in used.payers
        ],
        "payments": [
            {
                "amount": _value_out(p.amount),
                "div293_income": _value_out(p.div293_income),
                "estimated_from_year_0": p.estimated_from_year_0,
                "for_fy": p.for_fy,
                "low_tax_contributions": _value_out(p.low_tax_contributions),
                "member_id": p.member_id,
                "modelled": p.modelled,
                "paid_year": p.paid_year,
                "payer": p.payer,
                "rate": p.rate.to_percent(),
                "taxable_income": _value_out(p.taxable_income),
                "threshold": _value_out(p.threshold),
            }
            for p in used.payments
        ],
        "rule_pack_version": used.rule_pack_version,
        "years": [
            {
                "fy": y.fy,
                "frozen_eroded": y.frozen_eroded,
                "members": [
                    {
                        "gross": _value_out(m.gross),
                        "member_id": m.member_id,
                        "take_home": _value_out(m.take_home),
                        "tax": _value_out(m.tax),
                    }
                    for m in y.members
                ],
                "source_fy": y.source_fy,
                "t": y.t,
            }
            for y in used.years
        ],
    }


def _debits_payload(used: Sequence[tax_income.DebitUsed]) -> list[dict[str, Any]]:
    """ADR-0028 s5: each line debit with the source that produced it (for Division 293: the Member,
    the financial year, the emission amount, the year-0 stand-in flag, the payer Assumption's layer
    and the Rule pack version), so a later change never alters a saved run."""
    return [
        {
            "amount": _value_out(u.debit.amount),
            "category_key": u.debit.category_key,
            "estimated_from_year_0": u.estimated_from_year_0,
            "for_fy": u.for_fy,
            "member_id": u.debit.member_id,
            "payer_layer": u.payer_layer,
            "reason": u.debit.reason,
            "ref": u.debit.ref,
            "rule_pack_version": u.rule_pack_version,
            "year": u.debit.year,
        }
        for u in used
    ]


def _money_in(raw: Mapping[str, Any]) -> Money:
    return Money(raw["minor"], raw["ccy"])


def _debits_from(provenance: Mapping[str, Any]) -> tuple[tax_income.DebitUsed, ...] | None:
    raw = provenance.get("debits")
    if raw is None:
        return None
    return tuple(
        tax_income.DebitUsed(
            pinput.LineDebit(
                d["member_id"],
                d["category_key"],
                d["year"],
                _money_in(d["amount"]),
                d["reason"],
                d["ref"],
            ),
            d["for_fy"],
            d["estimated_from_year_0"],
            d["payer_layer"],
            d["rule_pack_version"],
        )
        for d in raw
    )


def _tax_from(provenance: Mapping[str, Any]) -> tax_income.TaxUsed | None:
    raw = provenance.get("tax")
    if raw is None:
        return None
    return tax_income.TaxUsed(
        raw["engine_version"],
        raw["input_version"],
        raw["rule_pack_version"],
        tuple(
            tax_income.YearUsed(
                y["t"],
                y["fy"],
                tuple(
                    tax_income.MemberYear(
                        m["member_id"],
                        _money_in(m["gross"]),
                        _money_in(m["tax"]),
                        _money_in(m["take_home"]),
                    )
                    for m in y["members"]
                ),
                y["source_fy"],
                y["frozen_eroded"],
            )
            for y in raw["years"]
        ),
        tuple(
            tax_income.Div293Used(
                p["member_id"],
                p["for_fy"],
                _money_in(p["amount"]),
                p["payer"],
                p["paid_year"],
                p["modelled"],
                p["estimated_from_year_0"],
                _money_in(p["taxable_income"]),
                _money_in(p["low_tax_contributions"]),
                _money_in(p["div293_income"]),
                _money_in(p["threshold"]),
                Rate.from_percent(p["rate"]),
            )
            for p in raw["payments"]
        ),
        tuple(tax_income.PayerUsed(p["member_id"], p["payer"], p["layer"]) for p in raw["payers"]),
        raw["div293_unavailable"],
    )


def _income_from(provenance: Mapping[str, Any]) -> income.IncomeUsed | None:
    raw = provenance.get("income")
    if raw is None:
        return None

    def day(value: str | None) -> date | None:
        return date.fromisoformat(value) if value else None

    streams = tuple(
        income.StreamUsed(
            income.Stream(
                s["id"],
                s["name"],
                s["type_key"],
                s["member_id"],
                Money(s["gross_annual"]["minor"], s["gross_annual"]["ccy"]),
                Money(s["take_home_annual"]["minor"], s["take_home_annual"]["ccy"]),
                s["credit_category"],
                day(s["start_on"]),
                s["start_event"],
                day(s["end_on"]),
                s["end_event"],
            ),
            income.Span(s["from_year"], s["until_year"]),
            Rate.from_percent(s["real_wage_growth"]),
        )
        for s in raw["streams"]
    )
    rates = tuple(
        income.RateUsed(
            r["key"],
            r["from_year"],
            r["fy"],
            Rate.from_percent(r["value"]),
            r["layer"],
            r["source"],
        )
        for r in raw["rates"]
    )
    return income.IncomeUsed(streams, rates)


def _life_events_from(provenance: Mapping[str, Any]) -> tuple[life_events.LifeEvent, ...] | None:
    raw = provenance.get("life_events")
    if raw is None:
        return None
    return tuple(
        life_events.LifeEvent(
            e["member_id"],
            e["kind"],
            date.fromisoformat(e["on"]),
            Age.parse(e["age"]),
            e["source"],
            e["year"],
        )
        for e in raw
    )


def _spending_from(
    provenance: Mapping[str, Any],
) -> tuple[tuple[spending.Phase, ...] | None, str | None]:
    raw = provenance.get("spending")
    if raw is None:
        return None, None
    phases = tuple(
        spending.Phase(
            p["anchor"],
            p["offset_years"],
            Money(p["annual_amount"]["minor"], p["annual_amount"]["ccy"]),
        )
        for p in raw["phases"]
    )
    return phases, raw["layer"]


def _rules_payload(used: rules.RulesUsed) -> dict[str, Any]:
    """ADR-0018 s4: every rule value used, with where it came from. Ages as ``67`` / ``66y6m``."""
    payload: dict[str, Any] = {
        "pack_version": used.pack_version,
        "parameters": [
            {"key": p.key, "label": p.label, "source": p.source} for p in used.parameters
        ],
        "values": [
            {"key": v.key, "layer": v.layer, "member_id": v.member_id, "value": str(v.value)}
            for v in used.values
        ],
    }
    if used.captured:  # ADR-0022: absent when the run used only shipped figures, as before
        payload["captured"] = [
            {"capture_id": c.capture_id, "fy": c.fy, "key": c.key} for c in used.captured
        ]
    return payload


def _rules_from(provenance: Mapping[str, Any]) -> rules.RulesUsed | None:
    raw = provenance.get("rules")
    if raw is None:
        return None
    return rules.RulesUsed(
        raw["pack_version"],
        tuple(rules.RuleParameter(p["key"], p["label"], p["source"]) for p in raw["parameters"]),
        tuple(
            rules.RuleValue(v["member_id"], v["key"], Age.parse(v["value"]), v["layer"])
            for v in raw["values"]
        ),
        tuple(
            rules.CapturedUsed(c["key"], c["fy"], c["capture_id"]) for c in raw.get("captured", [])
        ),
    )


def _provenance_of(row: RunRow) -> Mapping[str, Any]:
    return pinput.as_obj(result_json.loads(row.provenance_json), "provenance")


def _prepared_from(
    provenance: Mapping[str, Any], projection_input: pinput.ProjectionInput
) -> service.Prepared:
    return service.Prepared(
        projection_input.as_at,
        projection_input,
        tuple((c["key"], c["name"]) for c in provenance["categories"]),
        tuple(
            service.MemberInfo(m["member_id"], m["name"], m["age_now"], m["retirement_year"])
            for m in provenance["members"]
        ),
        tuple(
            service.FlaggedAccount(f["account_id"], f["name"], f["category_key"], f["status"])
            for f in provenance["flagged"]
        ),
        provenance["first_retirement_year"],
        provenance["last_retirement_year"],
        _rules_from(provenance),
        _life_events_from(provenance),
        _spending_from(provenance)[0],
        _income_from(provenance),
        _tax_from(provenance),
        _debits_from(provenance),
    )


def _sources_from(provenance: Mapping[str, Any]) -> tuple[assumptions.Source, ...]:
    return tuple(
        assumptions.Source(
            a["key"], a["scope_type"], a["scope_ref"], _value_in(a["key"], a["value"]), a["layer"]
        )
        for a in provenance["assumptions"]
    )


# --- writes --------------------------------------------------------------------------------


def _clean_label(label: str | None) -> str | None:
    clean = (label or "").strip() or None
    if clean is not None and len(clean) > LABEL_MAX:
        raise ValidationError(
            [FieldError("label", f"Label must be at most {LABEL_MAX} characters.")]
        )
    return clean


def _header(row: RunRow) -> RunHeader:
    return RunHeader(
        row.id,
        row.created_at,
        row.label,
        row.scenario_id,
        row.scenario_name,
        row.engine_version,
        row.funds_last_to_age,
        row.lasts_beyond_horizon,
        row.rule_pack_version,
    )


def _insert(
    session: Session,
    context: AuditContext,
    *,
    scenario_id: int,
    scenario_name: str,
    label: str | None,
    prepared: service.Prepared,
    result: engine.ProjectionResult,
    most: SustainableSpending,
    sources: Sequence[assumptions.Source],
    spending_layer: str | None,
    summary: str,
    extra: dict[str, Any],
) -> RunRow:
    row = RunRow(
        created_at=datetime.now(UTC).replace(tzinfo=None),
        label=label,
        scenario_id=scenario_id,
        scenario_name=scenario_name,
        engine_version=result.engine_version,
        input_version=prepared.input.input_version,
        rule_pack_version=prepared.rules.pack_version if prepared.rules else None,
        input_json=pinput.to_json(prepared.input),
        result_json=result_json.result_to_json(result),
        sustainable_json=result_json.sustainable_to_json(most),
        provenance_json=result_json.dumps(_provenance_payload(prepared, sources, spending_layer)),
        funds_last_to_age=result.funds_last_to_age,
        lasts_beyond_horizon=result.lasts_beyond_horizon,
    )
    session.add(row)
    session.flush()
    # Header fields only, never the payload (ADR-0016 s5).
    audit.record(
        session,
        context,
        entity_type="projection_run",
        entity_id=row.id,
        action="create",
        after={
            "label": row.label,
            "scenario_id": row.scenario_id,
            "scenario_name": row.scenario_name,
            "engine_version": row.engine_version,
            "input_version": row.input_version,
            "funds_last_to_age": row.funds_last_to_age,
            "lasts_beyond_horizon": row.lasts_beyond_horizon,
            "rule_pack_version": row.rule_pack_version,
        }
        | extra,
        summary=summary,
    )
    return row


def save_run(
    session: Session,
    context: AuditContext,
    scenario_id: int,
    label: str | None = None,
    as_at: date | None = None,
) -> RunHeader:
    """Resolve and project a Scenario now, and save the lot as one immutable run."""
    clean = _clean_label(label)
    current = service.run_scenario(session, scenario_id, as_at)
    name = current.scenario.name
    row = _insert(
        session,
        context,
        scenario_id=scenario_id,
        scenario_name=name,
        label=clean,
        prepared=current.run.prepared,
        result=current.run.result,
        most=current.run.sustainable,
        sources=current.provenance,
        spending_layer=current.spending_layer,
        summary=f"Saved a projection run of '{name}'" + (f" ({clean})" if clean else ""),
        extra={},
    )
    return _header(row)


def rerun_with_current_engine(session: Session, context: AuditContext, run_id: int) -> RunHeader:
    """Project a run's stored input with the current engine and save the outcome as a new run.

    The stored run is untouched. Nothing live is read: the input is the snapshot, upcast to the
    current input version if it is older.
    """
    old = _row(session, run_id)
    if old.engine_version == engine.ENGINE_VERSION:
        raise ValidationError(
            [FieldError("run", "This run was computed by the current engine already.")]
        )
    provenance = _provenance_of(old)
    projection_input = pinput.from_json(old.input_json)
    prepared = _prepared_from(provenance, projection_input)
    try:
        result = engine.project(projection_input)
        # A run saved before spending phases spent one constant amount from the last retirement,
        # which a single phase of any amount searches the same way.
        phases = prepared.spending or (
            spending.Phase(spending.LAST_RETIREMENT, 0, Money(1, projection_input.currency)),
        )
        most = sustainable_spending(
            projection_input, phases, prepared.last_retirement_year, prepared.fixed_outflows
        )
    except MoneyError:
        raise ValidationError(
            [FieldError("run", "These figures are too large to project.")]
        ) from None
    row = _insert(
        session,
        context,
        scenario_id=old.scenario_id,
        scenario_name=old.scenario_name,
        label=f"Re-run of run {old.id}",
        prepared=prepared,
        result=result,
        most=most,
        sources=_sources_from(provenance),
        spending_layer=_spending_from(provenance)[1],
        summary=f"Re-ran projection run {old.id} of '{old.scenario_name}' with engine "
        f"v{engine.ENGINE_VERSION}",
        extra={"rerun_of": old.id},
    )
    return _header(row)


# --- reads ---------------------------------------------------------------------------------


def _row(session: Session, run_id: int) -> RunRow:
    row = session.get(RunRow, run_id)
    if row is None:
        raise NotFoundError("Projection run not found.")
    return row


def list_runs(session: Session) -> list[RunHeader]:
    """Newest first. Selects the header columns only: no payload is read."""
    columns = (
        RunRow.id,
        RunRow.created_at,
        RunRow.label,
        RunRow.scenario_id,
        RunRow.scenario_name,
        RunRow.engine_version,
        RunRow.funds_last_to_age,
        RunRow.lasts_beyond_horizon,
        RunRow.rule_pack_version,
    )
    return [RunHeader(*r) for r in session.execute(select(*columns).order_by(RunRow.id.desc()))]


def get_run(session: Session, run_id: int) -> SavedRun:
    """A run rebuilt from its stored snapshot alone."""
    row = _row(session, run_id)
    provenance = _provenance_of(row)
    prepared = _prepared_from(provenance, pinput.from_json(row.input_json))
    if row.sustainable_json is None:
        raise ValueError(f"Projection run {row.id} stores no sustainable-spending output")
    run = service.ProjectionRun(
        prepared,
        result_json.result_from_json(row.result_json),
        result_json.sustainable_from_json(row.sustainable_json),
    )
    return SavedRun(_header(row), run, _sources_from(provenance), _spending_from(provenance)[1])
