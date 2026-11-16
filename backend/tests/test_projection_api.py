"""The first projection end to end: HTTP, service, real temporary SQLite, engine (ADR-0015 s8).

The synthetic household: Alex (born 1984-03-12) and Jordan (born 1986-09-30), with Cash, an
Investments brokerage Account, Superannuation (Alex) and an overseas pension (Jordan, in GBP).
Property, the car and the home loan are not projected.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from finance360.capabilities.household.service import household_today
from finance360.capabilities.projection import service, spending
from finance360.config import Settings
from finance360.core.errors import ValidationError
from finance360.core.models.audit import AuditEvent
from finance360.core.money import Money
from finance360.core.rate import Rate
from finance360.main import create_app
from support import TestClient, owner_client, without_income_streams

REPO = Path(__file__).resolve().parents[2]
AS_AT = "2026-10-25"
ALEX, JORDAN = 1, 2
CATEGORIES = ("cash", "investments", "superannuation", "overseas_pensions")


@pytest.fixture
def sample(tmp_path: Path) -> Iterator[TestClient]:
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(REPO / "fixtures" / "sample_household" / "household.json", data / "household.json")
    settings = Settings(data_dir=data, port=8000, git_sha="dev", static_dir=None)
    with owner_client(create_app(settings)) as c:
        yield without_income_streams(c)


def phase(amount: Any, anchor: str = "last_retirement", offset: Any = 0) -> dict[str, Any]:
    return {"anchor": anchor, "offset_years": offset, "annual_amount": amount}


def body(**changes: Any) -> dict[str, Any]:
    """The request. ``annual_spending=`` is shorthand for one phase from the last retirement."""
    if "annual_spending" in changes:
        changes["spending_phases"] = [phase(changes.pop("annual_spending"))]
    base: dict[str, Any] = {
        "as_at": AS_AT,
        "inflation": "2.5",
        "returns": [
            {"category": "cash", "rate": "3"},
            {"category": "investments", "rate": "7"},
            {"category": "superannuation", "rate": "7"},
            {"category": "overseas_pensions", "rate": "6"},
        ],
        "retirement_ages": [{"member_id": ALEX, "age": 60}, {"member_id": JORDAN, "age": 60}],
        "spending_phases": [phase("70000.00")],
        "annual_saving": None,
        "horizon_age": 95,
    }
    return base | changes


def run(client: TestClient, **changes: Any) -> dict[str, Any]:
    res = client.post("/api/projection/run", json=body(**changes))
    assert res.status_code == 200, res.text
    return res.json()


def errors_of(client: TestClient, **changes: Any) -> dict[str, str]:
    res = client.post("/api/projection/run", json=body(**changes))
    assert res.status_code == 422, res.text
    return {e["field"]: e["message"] for e in res.json()["errors"]}


def cents(text: str) -> int:
    whole, _, frac = text.partition(".")
    return int(whole) * 100 + (-int(frac) if whole.startswith("-") else int(frac))


# --- the starting Position ----------------------------------------------------------------


def test_year_zero_opens_on_the_positions_figures_by_category(sample: TestClient) -> None:
    out = run(sample)
    position = sample.get("/api/position", params={"on": AS_AT}).json()
    expected = {
        k["key"]: k["figures"]["household"]
        for cls in position["classes"]
        for k in cls["categories"]
        if k["key"] in CATEGORIES
    }
    first = out["rows"][0]
    assert {c["category_key"]: c["opening"] for c in first["categories"]} == expected
    assert cents(first["opening"]) == sum(cents(v) for v in expected.values())
    # Property, the car and the loan are not projected.
    assert [c["key"] for c in out["categories"]] == list(CATEGORIES)


def test_the_headline_is_the_youngest_members_age(sample: TestClient) -> None:
    out = run(sample)
    assert out["reference_member"] == {
        "id": JORDAN,
        "name": "Jordan Sample",
        "age_now": 40,
        "retirement_year": 20,
    }
    assert out["years"] == 55 and len(out["rows"]) == 55
    assert (out["engine_version"], out["input_version"]) == (2, 2)
    first_short = next(r for r in out["rows"] if cents(r["shortfall"]) > 0)
    assert out["funds_last_to_age"] == first_short["age"] and not out["lasts_beyond_horizon"]
    assert all(cents(r["shortfall"]) == 0 for r in out["rows"] if r["t"] < first_short["t"])


def test_a_modest_spend_lasts_beyond_the_horizon(sample: TestClient) -> None:
    out = run(sample, annual_spending="10000.00")
    assert out["lasts_beyond_horizon"] and out["funds_last_to_age"] is None
    assert all(cents(r["shortfall"]) == 0 for r in out["rows"])


def test_every_row_satisfies_the_identities_by_category(sample: TestClient) -> None:
    out = run(sample, annual_saving="12000.00")
    for row in out["rows"]:
        for c in row["categories"]:
            assert cents(c["closing"]) == cents(c["opening"]) + cents(c["growth"]) + cents(
                c["credited"]
            ) - cents(c["withdrawn"])
        assert sum(cents(c["withdrawn"]) for c in row["categories"]) + cents(
            row["shortfall"]
        ) == cents(row["need"])
        assert sum(cents(c["closing"]) for c in row["categories"]) == cents(row["closing"])


# --- the phasing simplifications ----------------------------------------------------------


def test_spending_starts_only_when_both_members_are_retired_and_saving_stops_at_the_first(
    sample: TestClient,
) -> None:
    # Alex is 42 and retires at 60 (year 18); Jordan is 40 and retires at 60 (year 20).
    out = run(sample, annual_saving="20000.00")
    needs = [cents(r["need"]) for r in out["rows"]]
    assert needs[:20] == [0] * 20 and set(needs[20:]) == {7_000_000}
    credits = [
        cents(next(c for c in r["categories"] if c["category_key"] == "investments")["credited"])
        for r in out["rows"]
    ]
    assert (
        credits[:18] == [2_000_000] * 18
    )  # split equally across the two Members, into Investments
    assert set(credits[18:]) == {0}  # none after the first retirement, nor between the two
    assert {m["id"]: m["retirement_year"] for m in out["members"]} == {ALEX: 18, JORDAN: 20}


def test_super_and_overseas_pension_are_untouched_until_their_owners_retire(
    sample: TestClient,
) -> None:
    # Alex retires at 50 (year 8) but Jordan not until 70 (year 30): spending starts in year 30, so
    # nothing is drawn before then, whatever is accessible.
    out = run(
        sample,
        retirement_ages=[{"member_id": ALEX, "age": 50}, {"member_id": JORDAN, "age": 70}],
    )
    assert all(cents(r["need"]) == 0 for r in out["rows"][:30])
    assert all(cents(c["withdrawn"]) == 0 for r in out["rows"][:30] for c in r["categories"])
    assert cents(out["rows"][30]["need"]) == 7_000_000


def test_an_already_retired_member_has_retirement_year_zero(sample: TestClient) -> None:
    out = run(
        sample,
        retirement_ages=[{"member_id": ALEX, "age": 30}, {"member_id": JORDAN, "age": 30}],
        annual_saving="5000.00",
    )
    assert {m["retirement_year"] for m in out["members"]} == {0}
    assert cents(out["rows"][0]["need"]) == 7_000_000
    assert all(cents(c["credited"]) == 0 for c in out["rows"][0]["categories"])  # no saving


def test_saving_with_an_odd_cent_splits_exactly(sample: TestClient) -> None:
    out = run(sample, annual_saving="10000.01")
    credited = cents(
        next(c for c in out["rows"][0]["categories"] if c["category_key"] == "investments")[
            "credited"
        ]
    )
    assert credited == 1_000_001


# --- the Accounts left out ----------------------------------------------------------------


def test_unvalued_accounts_are_listed_and_left_out(sample: TestClient) -> None:
    # On 2024-10-20 four Accounts have no Valuation yet (see test_position_history).
    out = run(sample, as_at="2024-10-20")
    assert out["flagged"], "Accounts without a Valuation are listed"
    assert {f["status"] for f in out["flagged"]} == {"no_valuation"}
    assert {f["category_key"] for f in out["flagged"]} <= set(CATEGORIES)
    position = sample.get("/api/position", params={"on": "2024-10-20"}).json()
    projected = {
        k["key"]: k["figures"]["household"]
        for cls in position["classes"]
        for k in cls["categories"]
        if k["key"] in CATEGORIES
    }
    first = {c["category_key"]: c["opening"] for c in out["rows"][0]["categories"]}
    assert all(first[key] == projected.get(key, "0.00") for key in CATEGORIES)


# --- validation ---------------------------------------------------------------------------


def test_bad_text_is_a_422_with_the_shared_error_shape_per_field(sample: TestClient) -> None:
    res = sample.post(
        "/api/projection/run",
        json=body(
            inflation="2.55555",
            annual_spending="12.345",
            returns=[{"category": "cash", "rate": "abc"}],
        ),
    )
    assert res.status_code == 422
    errors = {e["field"]: e["message"] for e in res.json()["errors"]}
    assert set(errors) == {"inflation", "spending_phases.0.annual_amount", "returns.cash"}
    assert "4 decimal places" in errors["inflation"]


def test_missing_assumptions_are_named_by_field(sample: TestClient) -> None:
    errors = errors_of(
        sample,
        returns=[{"category": "cash", "rate": "3"}],
        retirement_ages=[{"member_id": ALEX, "age": 60}],
    )
    assert set(errors) == {
        "returns.investments",
        "returns.superannuation",
        "returns.overseas_pensions",
        f"retirement_ages.{JORDAN}",
    }


def test_the_horizon_must_be_above_the_youngest_members_age(sample: TestClient) -> None:
    assert "above 40" in errors_of(sample, horizon_age=40)["horizon_age"]
    assert "horizon_age" in errors_of(sample, horizon_age=161)
    assert "retirement_ages.1" in errors_of(
        sample, retirement_ages=[{"member_id": ALEX, "age": 121}, {"member_id": JORDAN, "age": 60}]
    )


def test_negative_amounts_and_unknown_categories_are_refused(sample: TestClient) -> None:
    assert "spending_phases.0.annual_amount" in errors_of(sample, annual_spending="-1.00")
    assert "spending_phases.0.annual_amount" in errors_of(sample, annual_spending="0.00")
    assert "annual_saving" in errors_of(sample, annual_saving="-1.00")
    assert "returns.property" in errors_of(
        sample, returns=body()["returns"] + [{"category": "property", "rate": "4"}]
    )
    assert "inflation" in errors_of(sample, inflation="100.5")


def test_json_numbers_for_percentages_and_amounts_are_refused(sample: TestClient) -> None:
    for changes in (
        {"inflation": 2.5},
        {"annual_spending": 70000},
        {"spending_phases": [phase("1.00", offset=1.5)]},
        {"annual_saving": 100.5},
        {"returns": [{"category": "cash", "rate": 3}]},
        {"horizon_age": 95.5},
        {"horizon_age": "95"},
    ):
        res = sample.post("/api/projection/run", json=body(**changes))
        assert res.status_code == 422, changes
        assert "errors" in res.json()


def test_figures_too_large_to_project_are_a_422_not_a_crash(sample: TestClient) -> None:
    errors = errors_of(sample, annual_saving="90000000000000000.00")
    assert "too large" in errors["body"]


def test_a_household_with_no_members_cannot_be_projected(tmp_path: Path) -> None:
    with owner_client(
        create_app(Settings(data_dir=tmp_path / "d", port=8000, git_sha="dev", static_dir=None))
    ) as c:
        assert "Member" in errors_of(c, retirement_ages=[])["body"]


def test_running_a_projection_stores_nothing(sample: TestClient) -> None:
    factory: sessionmaker[Session] = sample.app.state.session_factory  # type: ignore[attr-defined]

    def events() -> int:
        with factory() as s:
            return s.scalar(select(func.count()).select_from(AuditEvent)) or 0

    before = events()
    run(sample)
    assert events() == before


def test_omitting_the_date_projects_from_today(sample: TestClient) -> None:
    payload = body()
    del payload["as_at"]
    res = sample.post("/api/projection/run", json=payload)
    assert res.status_code == 200, res.text
    factory: sessionmaker[Session] = sample.app.state.session_factory  # type: ignore[attr-defined]
    with factory() as session:
        assert res.json()["as_at"] == household_today(session).isoformat()  # the Household's zone


# --- the service directly -----------------------------------------------------------------


def request(**changes: Any) -> service.ProjectionRequest:
    base: dict[str, Any] = {
        "inflation": Rate(25_000),
        "returns": {k: Rate(50_000) for k in CATEGORIES},
        "retirement_ages": {ALEX: 60, JORDAN: 60},
        "spending_phases": (spending.Phase(spending.LAST_RETIREMENT, 0, Money(7_000_000, "AUD")),),
        "annual_saving": None,
        "horizon_age": 95,
        "as_at": date(2026, 10, 25),
    }
    return service.ProjectionRequest(**(base | changes))


def test_the_resolved_input_has_a_line_per_member_and_category_with_access_years(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory() as session:
        prepared = service.build_input(session, request())
    i = prepared.input
    assert i.drawdown_order == CATEGORIES
    assert len(i.lines) == 2 * 4
    access = {(x.member_id, x.category_key): x.accessible_from_year for x in i.lines}
    assert access[(ALEX, "cash")] == access[(JORDAN, "investments")] == 0
    assert access[(ALEX, "superannuation")] == 18 and access[(JORDAN, "overseas_pensions")] == 20
    assert [(o.from_year, o.until_year, o.amount.amount_minor) for o in i.outflows] == [
        (20, None, 7_000_000)
    ]
    assert i.years == 55 and i.currency == "AUD"
    # A Member with no balance in a Category still has a (zero) line to credit.
    zero = next(x for x in i.lines if (x.member_id, x.category_key) == (ALEX, "overseas_pensions"))
    assert zero.start.amount_minor == 0


def test_saving_is_split_equally_with_the_odd_cent_to_the_lowest_member(
    sample_factory: sessionmaker[Session],
) -> None:
    with sample_factory() as session:
        i = service.build_input(session, request(annual_saving=Money(1_000_001, "AUD"))).input
    assert [
        (x.member_id, x.category_key, x.amount.amount_minor, x.from_year, x.until_year)
        for x in i.inflows
    ] == [
        (ALEX, "investments", 500_001, 0, 18),
        (JORDAN, "investments", 500_000, 0, 18),
    ]


@pytest.mark.parametrize(
    "plan",
    [(), (spending.Phase(spending.LAST_RETIREMENT, 0, Money(0, "AUD")),)],
    ids=["no phases", "a stored 0 phase"],
)
def test_zero_spending_and_zero_saving_add_no_flows(
    sample_factory: sessionmaker[Session], plan: tuple[spending.Phase, ...]
) -> None:
    with sample_factory() as session:
        i = service.build_input(
            session, request(spending_phases=plan, annual_saving=Money(0, "AUD"))
        ).input
    assert i.inflows == () and i.outflows == ()


def test_each_phase_becomes_one_outflow_until_the_next_starts(
    sample_factory: sessionmaker[Session],
) -> None:
    plan = (
        spending.Phase(spending.LAST_RETIREMENT, 0, Money(7_000_000, "AUD")),
        spending.Phase(spending.LAST_RETIREMENT, 10, Money(5_500_000, "AUD")),
    )
    with sample_factory() as session:
        i = service.build_input(session, request(spending_phases=plan)).input
    # Both Members retire at 60, so the last retirement is year 20.
    assert [(o.from_year, o.until_year, o.amount.amount_minor) for o in i.outflows] == [
        (20, 30, 7_000_000),
        (30, None, 5_500_000),
    ]


def test_parse_request_collects_every_problem_at_once() -> None:
    with pytest.raises(ValidationError) as caught:
        service.parse_request(
            inflation="x",
            returns=[("cash", "1"), ("cash", "2"), ("equities", "3")],
            retirement_ages=[(1, 60), (1, 61)],
            spending_phases=[("last_retirement", 0, "1.234")],
            annual_saving="oops",
            horizon_age=90,
        )
    assert {e.field for e in caught.value.errors} == {
        "inflation",
        "returns.cash",
        "returns.equities",
        "retirement_ages.1",
        "spending_phases.0.annual_amount",
        "annual_saving",
    }


# --- sustainable retirement income (B2) -----------------------------------------------------


def money_text(minor: int) -> str:
    return f"{minor // 100}.{minor % 100:02d}"


def test_the_sustainable_amount_is_the_exact_boundary_through_the_api(sample: TestClient) -> None:
    sustainable = run(sample)["sustainable"]
    assert sustainable["capped"] is False
    best = cents(sustainable["annual_spending"])
    assert best > 0
    assert run(sample, annual_spending=money_text(best))["lasts_beyond_horizon"]
    assert not run(sample, annual_spending=money_text(best + 1))["lasts_beyond_horizon"]


def test_the_sustainable_amount_ignores_the_spending_entered(sample: TestClient) -> None:
    assert (
        run(sample, annual_spending="1.00")["sustainable"]
        == run(sample, annual_spending="900000.00")["sustainable"]
    )


def test_a_modest_spend_is_under_the_sustainable_amount(sample: TestClient) -> None:
    out = run(sample, annual_spending="10000.00")
    assert out["lasts_beyond_horizon"]
    assert cents(out["sustainable"]["annual_spending"]) >= 1_000_000


def test_spending_that_starts_after_the_horizon_is_capped(sample: TestClient) -> None:
    out = run(
        sample,
        retirement_ages=[{"member_id": ALEX, "age": 110}, {"member_id": JORDAN, "age": 110}],
    )
    assert out["sustainable"] == {"annual_spending": "1000000000.00", "capped": True}
