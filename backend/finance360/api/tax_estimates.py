"""Tax what-if and saved Tax estimate endpoints (ADR-0019 section 6). Thin: the rules
live in ``capabilities.tax.saved``.

Request amounts are decimal strings (never JSON numbers); days are whole numbers and flags are
booleans. Responses carry amounts as plain AUD decimal strings. A what-if is edited in place and
audited; a saved estimate is written once and never changes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, cast

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, Field, StrictBool, StrictInt

from finance360.api.household import ERRORS, ContextDep, SessionDep
from finance360.api.tax import (
    TaxHouseholdOut,
    TaxMemberOut,
    TaxProvisionalOut,
    TaxRecordIn,
    TaxUnmodelledOut,
    ValueOut,
    amount_out,
    member_out,
    record_input,
    unmodelled_out,
    value_out,
)
from finance360.capabilities.tax import compare, labels, saved, whatif
from finance360.capabilities.tax import input as tinput
from finance360.capabilities.tax.input import Total
from finance360.core.money import Money
from finance360.core.rulepack import fy_label

router = APIRouter(prefix="/api/tax", tags=["tax"])


# --- shapes ---------------------------------------------------------------------------------


class TaxValueOut(BaseModel):
    """A Label total or an amount: one of the three is set, by the label's kind."""

    amount: str | None
    days: int | None
    flag: bool | None


def _value(value: Total) -> TaxValueOut:
    held: ValueOut = value_out(value)
    return TaxValueOut(amount=held.amount, days=held.days, flag=held.flag)


class TaxOverrideIn(BaseModel):
    op: str = Field(
        description="'set' (a Member's Label total becomes the amount, absolute), 'exclude_label', "
        "'exclude_record' or 'add_record' (a hypothetical Tax record of any type)."
    )
    member_id: StrictInt | None = Field(default=None, description="set, exclude_label.")
    label: str | None = Field(default=None, description="The Return label key: set, exclude_label.")
    amount: str | None = Field(default=None, description="set, for an amount label: AUD decimal.")
    days: StrictInt | None = Field(default=None, description="set, for a days label.")
    flag: StrictBool | None = Field(default=None, description="set, for a yes or no label.")
    record_id: StrictInt | None = Field(default=None, description="exclude_record.")
    record: TaxRecordIn | None = Field(
        default=None, description="add_record: the record, as the register takes it."
    )


class TaxWhatIfIn(BaseModel):
    name: str = ""
    financial_year: StrictInt = Field(description="The year it ends in: 2027 is FY 2026-27.")
    notes: str | None = None
    overrides: list[TaxOverrideIn] = Field(
        default_factory=lambda: [],
        description="The whole list. Values are absolute, never deltas; a what-if never inherits "
        "from another.",
    )


class TaxOverrideOut(BaseModel):
    op: str
    member_id: int | None
    label_key: str | None
    label_name: str | None
    value: TaxValueOut | None = Field(description="set: the stated total.")
    record_id: int | None
    record: TaxRecordIn | None = Field(description="add_record: the hypothetical record.")


class TaxWhatIfOut(BaseModel):
    id: int
    name: str
    financial_year: int
    financial_year_label: str
    notes: str | None
    origin: str = Field(description="'user', or 'ai' for one an AI built from a question.")
    created_at: datetime
    overrides: list[TaxOverrideOut]
    estimate_count: int = Field(description="Saved estimates made from it; any blocks deleting it.")


class TaxEstimateRunIn(BaseModel):
    financial_year: StrictInt = Field(description="The year it ends in: 2027 is FY 2026-27.")
    dependants: StrictInt = Field(default=0, description="Dependent children.")
    member_ids: list[StrictInt] = Field(description="One or two Household Members.")
    whatif_id: StrictInt | None = Field(
        default=None, description="A Tax what-if of that year. Omit for the base (the register)."
    )


class TaxEstimateSaveIn(TaxEstimateRunIn):
    label: str | None = Field(default=None, description="An optional label, up to 100 characters.")


class TaxEstimateHeaderOut(BaseModel):
    id: int
    created_at: datetime
    label: str | None
    financial_year: int
    financial_year_label: str
    whatif_id: int | None
    whatif_name: str | None
    engine_version: int
    current_engine: bool = Field(description="False: computed by an older engine than today's.")
    rule_pack_version: str
    incomplete: bool
    has_provisional: bool


class TaxContributionDetailOut(BaseModel):
    record_id: int = Field(description="Negative for a hypothetical record of a what-if.")
    description: str
    value: TaxValueOut


class TaxTotalOut(BaseModel):
    member_id: int
    member_name: str
    label_key: str
    label_name: str
    return_item: str
    kind: str
    value: TaxValueOut
    overridden: bool = Field(description="True when a what-if stated this total.")
    source: str = Field(
        description="Where the figure came from: 'records' (the Tax register), 'prefill' (an "
        "Income stream's forecast, never a Record) or 'stated' (a what-if set it)."
    )
    contributions: list[TaxContributionDetailOut]


class TaxLabelValueOut(BaseModel):
    label_key: str
    label_name: str
    value: TaxValueOut


class TaxAppliedOverrideOut(BaseModel):
    op: str
    summary: str = Field(description="The override and its effect, in words.")
    member_id: int | None
    label_key: str | None
    label_name: str | None
    value: TaxValueOut | None = Field(description="set: the stated total.")
    replaced: TaxValueOut | None = Field(description="The total it replaced, if there was one.")
    record_id: int | None
    description: str | None
    contributions: list[TaxLabelValueOut] = Field(
        description="exclude_record: what the record contributed (now removed). add_record: what "
        "the hypothetical record contributes."
    )


class TaxPrefillOut(BaseModel):
    member_id: int
    member_name: str
    label_key: str
    label_name: str
    stream_id: int
    stream_name: str
    value: TaxValueOut = Field(description="What it adds to the base (zero when not counted).")
    status: str = Field(
        description="'counted'; 'records_win' (the register has records for this income, so the "
        "forecast is not counted); 'overridden' (a what-if replaced or left out the total); "
        "'no_label' (not counted, see text)."
    )
    text: str = Field(description="How the amount was reached, or why it is not counted.")


class TaxEstimateViewOut(BaseModel):
    """An estimate as calculated or as saved: its pinned input, result and provenance."""

    financial_year: int
    financial_year_label: str
    engine_version: int
    rule_pack_version: str | None
    dependants: int
    incomplete: bool
    provisional: list[TaxProvisionalOut]
    whatif_id: int | None
    whatif_name: str | None
    members: list[TaxMemberOut]
    household: TaxHouseholdOut
    unmodelled: list[TaxUnmodelledOut]
    totals: list[TaxTotalOut] = Field(description="The Label totals it used, with their records.")
    overrides: list[TaxAppliedOverrideOut]
    prefill: list[TaxPrefillOut] = Field(
        description="The Income stream lines considered for the base, counted or not, and why."
    )
    rerun_of: int | None = Field(description="Set when it re-ran an older estimate.")


class TaxSavedEstimateOut(BaseModel):
    estimate: TaxEstimateHeaderOut
    view: TaxEstimateViewOut
    reproduces: bool | None = Field(
        description="True: calculating the pinned input again gives exactly the stored result. "
        "Null: it was computed by an older engine."
    )


class TaxLabelDeltaOut(BaseModel):
    member_id: int
    member_name: str
    label_key: str
    label_name: str
    return_item: str
    kind: str
    a: TaxValueOut
    b: TaxValueOut
    delta: TaxValueOut | None = Field(description="b less a; null for a yes or no label.")


class TaxResultDeltaOut(BaseModel):
    key: str
    name: str
    member_id: int | None = Field(description="Null is the Household.")
    a: str
    b: str
    delta: str = Field(description="b less a; negative is less.")


class TaxComparedMemberOut(BaseModel):
    member_id: int
    name: str


class TaxComparisonOut(BaseModel):
    financial_year: int
    financial_year_label: str
    a: TaxEstimateHeaderOut
    b: TaxEstimateHeaderOut
    members: list[TaxComparedMemberOut]
    labels: list[TaxLabelDeltaOut] = Field(description="Only the totals that differ.")
    results: list[TaxResultDeltaOut] = Field(
        description="Every result line for each Member and the Household."
    )
    dependants_a: int
    dependants_b: int
    same_engine: bool
    rule_pack_differs: bool


RESULT_NAMES = {
    "taxable_income": "Taxable income",
    "tax_on_taxable_income": "Tax on taxable income",
    "offsets": "Offsets",
    "medicare_levy": "Medicare levy",
    "medicare_levy_surcharge": "Medicare levy surcharge",
    "help_repayment": "HELP repayment",
    "total_tax": "Total tax",
    "credits": "Credits",
    "net_outcome": "Net outcome",
}


# --- conversions ---------------------------------------------------------------------------


def _when(moment: datetime) -> datetime:
    return moment.replace(tzinfo=UTC)


def _override_in(item: TaxOverrideIn) -> saved.OverrideInput:
    return saved.OverrideInput(
        op=item.op,
        member_id=item.member_id,
        label=item.label,
        amount=item.amount,
        days=item.days,
        flag=item.flag,
        record_id=item.record_id,
        record=record_input(item.record) if item.record is not None else None,
    )


def _whatif_input(body: TaxWhatIfIn) -> saved.WhatIfInput:
    return saved.WhatIfInput(
        name=body.name,
        financial_year=body.financial_year,
        notes=body.notes,
        overrides=[_override_in(o) for o in body.overrides],
    )


def _override_out(item: whatif.Override) -> TaxOverrideOut:
    if isinstance(item, whatif.SetTotal):
        label = labels.LABELS[item.label]
        return TaxOverrideOut(
            op=whatif.SET,
            member_id=item.member_id,
            label_key=label.key,
            label_name=label.name,
            value=_value(item.value),
            record_id=None,
            record=None,
        )
    if isinstance(item, whatif.ExcludeLabel):
        label = labels.LABELS[item.label]
        return TaxOverrideOut(
            op=whatif.EXCLUDE_LABEL,
            member_id=item.member_id,
            label_key=label.key,
            label_name=label.name,
            value=None,
            record_id=None,
            record=None,
        )
    if isinstance(item, whatif.ExcludeRecord):
        return TaxOverrideOut(
            op=whatif.EXCLUDE_RECORD,
            member_id=None,
            label_key=None,
            label_name=None,
            value=None,
            record_id=item.record_id,
            record=None,
        )
    return TaxOverrideOut(
        op=whatif.ADD_RECORD,
        member_id=None,
        label_key=None,
        label_name=None,
        value=None,
        record_id=None,
        record=TaxRecordIn.model_validate(dict(item.record)),
    )


def _whatif_out(view: saved.WhatIfView) -> TaxWhatIfOut:
    return TaxWhatIfOut(
        id=view.id,
        name=view.name,
        financial_year=view.financial_year,
        financial_year_label=fy_label(view.financial_year),
        notes=view.notes,
        origin=view.origin,
        created_at=_when(view.created_at),
        overrides=[_override_out(o) for o in view.overrides],
        estimate_count=view.estimate_count,
    )


def _header_out(header: saved.EstimateHeader) -> TaxEstimateHeaderOut:
    return TaxEstimateHeaderOut(
        id=header.id,
        created_at=_when(header.created_at),
        label=header.label,
        financial_year=header.financial_year,
        financial_year_label=fy_label(header.financial_year),
        whatif_id=header.whatif_id,
        whatif_name=header.whatif_name,
        engine_version=header.engine_version,
        current_engine=header.current_engine,
        rule_pack_version=header.rule_pack_version,
        incomplete=header.incomplete,
        has_provisional=header.has_provisional,
    )


def _stored_value(raw: Any) -> TaxValueOut:
    """A value as the provenance JSON holds it: ``{ccy, minor}``, a whole number or a boolean."""
    total: Total = (
        tinput.money_in(cast("dict[str, Any]", raw), "a value") if isinstance(raw, dict) else raw
    )
    return _value(total)


def _label_values(items: list[dict[str, Any]]) -> list[TaxLabelValueOut]:
    return [
        TaxLabelValueOut(
            label_key=i["label"],
            label_name=labels.LABELS[i["label"]].name,
            value=_stored_value(i["value"]),
        )
        for i in items
    ]


def _applied_out(effect: dict[str, Any], names: dict[int, str]) -> TaxAppliedOverrideOut:
    op = effect["op"]
    label = labels.LABELS[effect["label"]] if "label" in effect else None
    who = names.get(effect.get("member_id", 0), "")
    was = None if effect.get("replaced") is None else _stored_value(effect["replaced"])
    contributions = _label_values(effect.get("removed", effect.get("added", [])))
    if op == whatif.SET and label is not None:
        summary = f"{who}: {label.name} set to a stated total."
    elif op == whatif.EXCLUDE_LABEL and label is not None:
        summary = f"{who}: {label.name} left out."
    elif op == whatif.EXCLUDE_RECORD:
        summary = f"Left out the record '{effect['description']}'."
    else:
        summary = f"Added the hypothetical record '{effect['description']}' for {who}."
    return TaxAppliedOverrideOut(
        op=op,
        summary=summary,
        member_id=effect.get("member_id"),
        label_key=label.key if label else None,
        label_name=label.name if label else None,
        value=_stored_value(effect["value"]) if op == whatif.SET else None,
        replaced=was,
        record_id=effect.get("record_id"),
        description=effect.get("description"),
        contributions=contributions,
    )


def _view_out(body: saved.Body, header: saved.EstimateHeader | None) -> TaxEstimateViewOut:
    result = body.result
    names = {m["member_id"]: m["name"] for m in body.provenance["members"]}
    house = result.household
    pinned = body.provenance.get("whatif")
    totals: list[TaxTotalOut] = []
    for t in body.provenance["totals"]:
        label = labels.LABELS[t["label"]]
        totals.append(
            TaxTotalOut(
                member_id=t["member_id"],
                member_name=names.get(t["member_id"], ""),
                label_key=label.key,
                label_name=label.name,
                return_item=label.return_item,
                kind=label.kind,
                value=_stored_value(t["value"]),
                overridden=t["overridden"],
                source=t.get("source", "records"),
                contributions=[
                    TaxContributionDetailOut(
                        record_id=c["record_id"],
                        description=c["description"],
                        value=_stored_value(c["value"]),
                    )
                    for c in t["contributions"]
                ],
            )
        )
    return TaxEstimateViewOut(
        financial_year=result.financial_year,
        financial_year_label=fy_label(result.financial_year),
        engine_version=result.engine_version,
        rule_pack_version=header.rule_pack_version if header else _pack_label(body),
        dependants=body.input.dependants,
        incomplete=result.incomplete,
        provisional=[TaxProvisionalOut(key=k, note=n) for k, n in result.provisional],
        whatif_id=pinned["id"] if pinned else None,
        whatif_name=pinned["name"] if pinned else None,
        members=[member_out(m, names.get(m.member_id, "")) for m in result.members],
        household=TaxHouseholdOut(
            taxable_income=amount_out(house.taxable_income),
            tax_on_taxable_income=amount_out(house.tax_on_taxable_income),
            offsets=amount_out(house.offsets),
            medicare_levy=amount_out(house.medicare_levy),
            medicare_levy_surcharge=amount_out(house.medicare_levy_surcharge),
            help_repayment=amount_out(house.help_repayment),
            total_tax=amount_out(house.total_tax),
            credits=amount_out(house.credits),
            net_outcome=amount_out(house.net_outcome),
            surcharge_income=amount_out(house.surcharge_income),
            surcharge_thresholds=house.surcharge_thresholds,
            surcharge_tier=house.surcharge_tier,
        ),
        unmodelled=[unmodelled_out(u, names.get(u.member_id, "")) for u in result.unmodelled],
        totals=totals,
        overrides=[_applied_out(e, names) for e in body.provenance["overrides"]],
        prefill=[
            TaxPrefillOut(
                member_id=p["member_id"],
                member_name=names.get(p["member_id"], ""),
                label_key=p["label"],
                label_name=labels.LABELS[p["label"]].name if p["label"] else "",
                stream_id=p["stream_id"],
                stream_name=p["stream_name"],
                value=_stored_value(p["value"]),
                status=p["status"],
                text=p["text"],
            )
            for p in body.provenance.get("prefill", [])
        ],
        rerun_of=body.provenance.get("rerun_of"),
    )


def _pack_label(body: saved.Body) -> str | None:
    for entry in body.input.provenance:
        if entry.key == "rule_pack":
            return entry.detail
    return None


# --- Tax what-ifs ---------------------------------------------------------------------------


@router.get("/whatifs", response_model=list[TaxWhatIfOut], operation_id="listTaxWhatIfs")
def list_whatifs(
    session: SessionDep,
    financial_year: Annotated[int | None, Query(description="Only this year's.")] = None,
) -> list[TaxWhatIfOut]:
    return [_whatif_out(v) for v in saved.list_whatifs(session, financial_year)]


@router.post(
    "/whatifs",
    status_code=201,
    response_model=TaxWhatIfOut,
    operation_id="createTaxWhatIf",
    responses=ERRORS,  # type: ignore[arg-type]
)
def create_whatif(body: TaxWhatIfIn, session: SessionDep, ctx: ContextDep) -> TaxWhatIfOut:
    return _whatif_out(saved.create_whatif(session, ctx, _whatif_input(body)))


@router.get(
    "/whatifs/{whatif_id}",
    response_model=TaxWhatIfOut,
    operation_id="getTaxWhatIf",
    responses=ERRORS,  # type: ignore[arg-type]
)
def get_whatif(whatif_id: int, session: SessionDep) -> TaxWhatIfOut:
    return _whatif_out(saved.get_whatif(session, whatif_id))


@router.put(
    "/whatifs/{whatif_id}",
    response_model=TaxWhatIfOut,
    operation_id="updateTaxWhatIf",
    responses=ERRORS,  # type: ignore[arg-type]
)
def update_whatif(
    whatif_id: int, body: TaxWhatIfIn, session: SessionDep, ctx: ContextDep
) -> TaxWhatIfOut:
    return _whatif_out(saved.update_whatif(session, ctx, whatif_id, _whatif_input(body)))


@router.delete(
    "/whatifs/{whatif_id}",
    status_code=204,
    operation_id="deleteTaxWhatIf",
    responses=ERRORS,  # type: ignore[arg-type]
)
def delete_whatif(whatif_id: int, session: SessionDep, ctx: ContextDep) -> Response:
    saved.delete_whatif(session, ctx, whatif_id)
    return Response(status_code=204)


# --- estimates ------------------------------------------------------------------------------


@router.post(
    "/estimates/preview",
    response_model=TaxEstimateViewOut,
    operation_id="previewTaxEstimate",
    responses=ERRORS,  # type: ignore[arg-type]
)
def preview_estimate(body: TaxEstimateRunIn, session: SessionDep) -> TaxEstimateViewOut:
    """Calculate the base or a Tax what-if now. Nothing is saved."""
    return _view_out(
        saved.calculate(
            session,
            financial_year=body.financial_year,
            dependants=body.dependants,
            member_ids=body.member_ids,
            whatif_id=body.whatif_id,
        ),
        None,
    )


@router.post(
    "/estimates",
    status_code=201,
    response_model=TaxEstimateHeaderOut,
    operation_id="saveTaxEstimate",
    responses=ERRORS,  # type: ignore[arg-type]
)
def save_estimate(
    body: TaxEstimateSaveIn, session: SessionDep, ctx: ContextDep
) -> TaxEstimateHeaderOut:
    """Calculate the base or a Tax what-if now and save the whole result, once. It never changes."""
    return _header_out(
        saved.save_estimate(
            session,
            ctx,
            financial_year=body.financial_year,
            dependants=body.dependants,
            member_ids=body.member_ids,
            whatif_id=body.whatif_id,
            label=body.label,
        )
    )


@router.get(
    "/estimates", response_model=list[TaxEstimateHeaderOut], operation_id="listTaxEstimates"
)
def list_estimates(
    session: SessionDep,
    financial_year: Annotated[int | None, Query(description="Only this year's.")] = None,
) -> list[TaxEstimateHeaderOut]:
    """Saved estimates, newest first. Reads the header columns only, never the stored results."""
    return [_header_out(h) for h in saved.list_estimates(session, financial_year)]


@router.get(
    "/estimates/compare",
    response_model=TaxComparisonOut,
    operation_id="compareTaxEstimates",
    responses=ERRORS,  # type: ignore[arg-type]
)
def compare_estimates(
    session: SessionDep,
    a: Annotated[int, Query(description="The estimate to compare from.")],
    b: Annotated[int, Query(description="The estimate to compare to: deltas are b less a.")],
) -> TaxComparisonOut:
    """Per-label and per-result deltas of two saved estimates of the same financial year."""
    got = saved.compare_estimates(session, a, b)
    c = got.comparison
    return TaxComparisonOut(
        financial_year=c.financial_year,
        financial_year_label=fy_label(c.financial_year),
        a=_header_out(got.a),
        b=_header_out(got.b),
        members=[TaxComparedMemberOut(member_id=m, name=got.names[m]) for m in c.member_ids],
        labels=[_label_delta_out(d, got.names) for d in c.labels],
        results=[
            TaxResultDeltaOut(
                key=r.key,
                name=RESULT_NAMES[r.key],
                member_id=r.member_id,
                a=amount_out(r.a),
                b=amount_out(r.b),
                delta=amount_out(r.delta),
            )
            for r in c.results
        ],
        dependants_a=c.dependants[0],
        dependants_b=c.dependants[1],
        same_engine=c.same_engine,
        rule_pack_differs=c.rule_pack_differs,
    )


def _label_delta_out(d: compare.LabelDelta, names: dict[int, str]) -> TaxLabelDeltaOut:
    label = labels.LABELS[d.label]
    delta: Money | int | None = d.delta
    held = None
    if isinstance(delta, Money):
        held = _value(delta)
    elif delta is not None:
        held = TaxValueOut(amount=None, days=delta, flag=None)
    return TaxLabelDeltaOut(
        member_id=d.member_id,
        member_name=names.get(d.member_id, ""),
        label_key=label.key,
        label_name=label.name,
        return_item=label.return_item,
        kind=label.kind,
        a=_value(d.a),
        b=_value(d.b),
        delta=held,
    )


@router.get(
    "/estimates/{estimate_id}",
    response_model=TaxSavedEstimateOut,
    operation_id="getTaxEstimate",
    responses=ERRORS,  # type: ignore[arg-type]
)
def get_estimate(estimate_id: int, session: SessionDep) -> TaxSavedEstimateOut:
    """A saved estimate exactly as computed, rendered from its stored snapshot alone."""
    got = saved.get_estimate(session, estimate_id)
    return TaxSavedEstimateOut(
        estimate=_header_out(got.header),
        view=_view_out(got.body, got.header),
        reproduces=got.reproduces,
    )


@router.post(
    "/estimates/{estimate_id}/rerun",
    status_code=201,
    response_model=TaxEstimateHeaderOut,
    operation_id="rerunTaxEstimate",
    responses=ERRORS,  # type: ignore[arg-type]
)
def rerun_estimate(estimate_id: int, session: SessionDep, ctx: ContextDep) -> TaxEstimateHeaderOut:
    """Calculate a saved estimate's pinned input with the current engine; save a new estimate."""
    return _header_out(saved.rerun_with_current_engine(session, ctx, estimate_id))
