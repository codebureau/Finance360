"""Scenario and Assumption endpoints. Thin: the rules live in ``capabilities.projection``.

Values are strings, never JSON numbers: a percent for a rate, a whole number of years for an age,
a decimal amount for money. Their kind is the key's, from the registry in ``assumptions``.
"""

from __future__ import annotations

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field, StrictInt
from sqlalchemy.orm import Session

from finance360.api.household import ERRORS, ContextDep, SessionDep
from finance360.capabilities.projection import assumptions, scenarios, service
from finance360.core.models.scenario import Assumption, Scenario
from finance360.core.money import format_amount

router = APIRouter(tags=["scenarios"])


class DrawdownCategoryOut(BaseModel):
    key: str
    name: str


class DrawdownOrderIn(BaseModel):
    order: list[str] | None = Field(
        description="The whole order, every projected Category once. Null restores the default."
    )


class ScenarioOut(BaseModel):
    id: int
    key: str | None = Field(description="best, likely or worst for the built-in Scenarios.")
    name: str
    notes: str | None
    override_count: int = Field(description="How many Assumptions it states for itself.")
    drawdown_order: list[DrawdownCategoryOut] = Field(
        description="The order Categories fund spending in: the Scenario's own, or the default."
    )
    drawdown_is_default: bool = Field(description="True when the Scenario states no order.")
    spending_is_default: bool = Field(
        description="True when the Scenario states no spending phases of its own: it uses the "
        "Household default list."
    )


class ScenarioIn(BaseModel):
    name: str = ""
    notes: str | None = None
    copy_from: int | None = Field(
        default=None, description="Copy this Scenario's overrides. Omit for an empty Scenario."
    )


class ScenarioEditIn(BaseModel):
    name: str = ""
    notes: str | None = None


class AssumptionOut(BaseModel):
    key: str
    scope_type: str
    scope_ref: str
    value: str = Field(description="A percent, a whole number of years, or a decimal amount.")
    overridden_by: list[str] = Field(
        description="Household defaults only: the Scenarios that state their own value."
    )


class LayerOut(BaseModel):
    scenario_id: int | None = Field(description="Null is the Household defaults.")
    rows: list[AssumptionOut]


class ChangeIn(BaseModel):
    key: str
    scope_type: str
    scope_ref: str = ""
    value: str | None = Field(default=None, description="Null removes the row.")


class AssumptionsIn(BaseModel):
    scenario_id: int | None = Field(default=None, description="Omit for the Household defaults.")
    changes: list[ChangeIn]


class ReviewOut(BaseModel):
    key: str
    scope_type: str
    scope_ref: str
    scenarios: list[str]


class AssumptionsSavedOut(LayerOut):
    review: list[ReviewOut] = Field(
        description="Household defaults changed while these Scenarios override them."
    )


def _scenario_out(
    scenario: Scenario, counts: dict[int, int], own_spending: set[int]
) -> ScenarioOut:
    own = scenarios.drawdown_order_of(scenario)
    order = own if own is not None else service.DRAWDOWN_ORDER
    return ScenarioOut(
        drawdown_order=[DrawdownCategoryOut(key=k, name=service.CATEGORY_NAMES[k]) for k in order],
        drawdown_is_default=own is None,
        spending_is_default=scenario.id not in own_spending,
        id=scenario.id,
        key=scenario.key,
        name=scenario.name,
        notes=scenario.notes,
        override_count=counts.get(scenario.id, 0),
    )


def _layer(session: Session, scenario_id: int | None) -> LayerOut:
    if scenario_id is not None:
        scenarios.get_scenario(session, scenario_id)

    def out(row: Assumption) -> AssumptionOut:
        stated = scenarios.to_row(row)
        by = (
            list(scenarios.overriding_scenarios(session, row.key, row.scope_type, row.scope_ref))
            if scenario_id is None
            else []
        )
        return AssumptionOut(
            key=row.key,
            scope_type=row.scope_type,
            scope_ref=row.scope_ref,
            value=assumptions.format_value(stated.value),
            overridden_by=by,
        )

    return LayerOut(
        scenario_id=scenario_id, rows=[out(r) for r in scenarios.layer_rows(session, scenario_id)]
    )


@router.get("/api/scenarios", response_model=list[ScenarioOut], operation_id="listScenarios")
def list_scenarios(session: SessionDep) -> list[ScenarioOut]:
    counts = scenarios.override_counts(session)
    own = set(scenarios.scenarios_with_own_spending(session))
    return [_scenario_out(s, counts, own) for s in scenarios.list_scenarios(session)]


@router.post(
    "/api/scenarios",
    status_code=201,
    response_model=ScenarioOut,
    operation_id="addScenario",
    responses=ERRORS,  # type: ignore[arg-type]
)
def add_scenario(body: ScenarioIn, session: SessionDep, ctx: ContextDep) -> ScenarioOut:
    scenario = scenarios.create_scenario(session, ctx, body.name, body.notes, body.copy_from)
    return _scenario_out(
        scenario,
        scenarios.override_counts(session),
        set(scenarios.scenarios_with_own_spending(session)),
    )


@router.put(
    "/api/scenarios/{scenario_id}",
    response_model=ScenarioOut,
    operation_id="updateScenario",
    responses=ERRORS,  # type: ignore[arg-type]
)
def update_scenario(
    scenario_id: int, body: ScenarioEditIn, session: SessionDep, ctx: ContextDep
) -> ScenarioOut:
    scenario = scenarios.update_scenario(session, ctx, scenario_id, body.name, body.notes)
    return _scenario_out(
        scenario,
        scenarios.override_counts(session),
        set(scenarios.scenarios_with_own_spending(session)),
    )


@router.put(
    "/api/scenarios/{scenario_id}/drawdown-order",
    response_model=ScenarioOut,
    operation_id="setDrawdownOrder",
    responses=ERRORS,  # type: ignore[arg-type]
)
def set_drawdown_order(
    scenario_id: int, body: DrawdownOrderIn, session: SessionDep, ctx: ContextDep
) -> ScenarioOut:
    scenario = scenarios.set_drawdown_order(session, ctx, scenario_id, body.order)
    return _scenario_out(
        scenario,
        scenarios.override_counts(session),
        set(scenarios.scenarios_with_own_spending(session)),
    )


@router.delete(
    "/api/scenarios/{scenario_id}",
    status_code=204,
    operation_id="deleteScenario",
    responses=ERRORS,  # type: ignore[arg-type]
)
def delete_scenario(scenario_id: int, session: SessionDep, ctx: ContextDep) -> Response:
    scenarios.delete_scenario(session, ctx, scenario_id)
    return Response(status_code=204)


@router.get(
    "/api/assumptions",
    response_model=LayerOut,
    operation_id="getAssumptions",
    responses=ERRORS,  # type: ignore[arg-type]
)
def get_assumptions(session: SessionDep, scenario_id: int | None = None) -> LayerOut:
    """One layer: a Scenario's own overrides, or (no ``scenario_id``) the Household defaults."""
    return _layer(session, scenario_id)


@router.put(
    "/api/assumptions",
    response_model=AssumptionsSavedOut,
    operation_id="setAssumptions",
    responses=ERRORS,  # type: ignore[arg-type]
)
def set_assumptions(
    body: AssumptionsIn, session: SessionDep, ctx: ContextDep
) -> AssumptionsSavedOut:
    reviews = scenarios.set_assumptions(
        session,
        ctx,
        body.scenario_id,
        [scenarios.Change(c.key, c.scope_type, c.scope_ref, c.value) for c in body.changes],
    )
    layer = _layer(session, body.scenario_id)
    return AssumptionsSavedOut(
        scenario_id=layer.scenario_id,
        rows=layer.rows,
        review=[
            ReviewOut(
                key=r.key,
                scope_type=r.scope_type,
                scope_ref=r.scope_ref,
                scenarios=list(r.scenarios),
            )
            for r in reviews
        ],
    )


class SpendingPhaseIn(BaseModel):
    anchor: str = Field(description="'start' (the projection start) or 'last_retirement'.")
    offset_years: StrictInt = Field(description="Whole years after the anchor, 0 or more.")
    annual_amount: str = Field(description="Today's dollars a year, above 0, as a decimal amount.")


class SpendingPhasesIn(BaseModel):
    scenario_id: int | None = Field(default=None, description="Omit for the Household default.")
    phases: list[SpendingPhaseIn] | None = Field(
        description="The whole list. For a Scenario, null or empty goes back to the Household "
        "default list; a list replaces the default as a whole, never a phase at a time."
    )


class SpendingPhaseOut(BaseModel):
    anchor: str
    offset_years: int
    annual_amount: str


class SpendingLayerOut(BaseModel):
    scenario_id: int | None = Field(description="Null is the Household default list.")
    phases: list[SpendingPhaseOut] = Field(description="In order: by anchor, then offset.")
    overridden_by: list[str] = Field(
        description="Household default only: the Scenarios that state their own list."
    )


class SpendingSavedOut(SpendingLayerOut):
    review: list[ReviewOut] = Field(
        description="The Household default list changed while these Scenarios state their own."
    )


def _spending_layer(session: Session, scenario_id: int | None) -> SpendingLayerOut:
    if scenario_id is not None:
        scenarios.get_scenario(session, scenario_id)
    return SpendingLayerOut(
        scenario_id=scenario_id,
        phases=[
            SpendingPhaseOut(
                anchor=p.anchor,
                offset_years=p.offset_years,
                annual_amount=format_amount(p.annual_amount),
            )
            for p in scenarios.layer_phases(session, scenario_id)
        ],
        overridden_by=list(scenarios.scenarios_with_own_spending(session).values())
        if scenario_id is None
        else [],
    )


@router.get(
    "/api/spending-phases",
    response_model=SpendingLayerOut,
    operation_id="getSpendingPhases",
    responses=ERRORS,  # type: ignore[arg-type]
)
def get_spending_phases(session: SessionDep, scenario_id: int | None = None) -> SpendingLayerOut:
    """One layer's spending phases: a Scenario's own list, or (no ``scenario_id``) the default."""
    return _spending_layer(session, scenario_id)


@router.put(
    "/api/spending-phases",
    response_model=SpendingSavedOut,
    operation_id="setSpendingPhases",
    responses=ERRORS,  # type: ignore[arg-type]
)
def set_spending_phases(
    body: SpendingPhasesIn, session: SessionDep, ctx: ContextDep
) -> SpendingSavedOut:
    """Replace one layer's whole spending phase list in one transaction."""
    reviews = scenarios.set_spending_phases(
        session,
        ctx,
        body.scenario_id,
        None
        if body.phases is None
        else [(p.anchor, p.offset_years, p.annual_amount) for p in body.phases],
    )
    layer = _spending_layer(session, body.scenario_id)
    return SpendingSavedOut(
        scenario_id=layer.scenario_id,
        phases=layer.phases,
        overridden_by=layer.overridden_by,
        review=[
            ReviewOut(
                key=r.key,
                scope_type=r.scope_type,
                scope_ref=r.scope_ref,
                scenarios=list(r.scenarios),
            )
            for r in reviews
        ],
    )
