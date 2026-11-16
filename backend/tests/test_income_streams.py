"""Income streams end to end (ADR-0017 s2, s4, s5): the CRUD service and API, the
salary and employer super flowing into a projection, the year-0 spending rule, saved runs, and the
Rule pack rates. HTTP, service, real temporary SQLite, engine. The synthetic household only.

The household: Alex (born 1984-03-12, age 42 at the as-at date) and Jordan (born 1986-09-30, age
40). Their invented salaries: Alex 120,000.00 gross and 91,000.00 take-home, Jordan 85,000.00 gross
and 66,000.00 take-home. At the as-at date (FY 2026-27) the pack gives SG 12% and contributions tax
15%, so year 0's employer super is 120,000.00 x 0.12 x 0.85 = 12,240.00 for Alex and
85,000.00 x 0.12 x 0.85 = 8,670.00 for Jordan (hand-checked).
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from finance360.capabilities.projection import income_streams
from finance360.config import Settings
from finance360.core.audit import AuditContext
from finance360.main import create_app
from support import TestClient, owner_client

REPO = Path(__file__).resolve().parents[2]
AS_AT = "2026-10-25"
ALEX, JORDAN = 1, 2
CTX = AuditContext("owner", "login:1", "api")


@pytest.fixture
def sample(tmp_path: Path) -> Iterator[TestClient]:
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(REPO / "fixtures" / "sample_household" / "household.json", data / "household.json")
    settings = Settings(data_dir=data, port=8000, git_sha="dev", static_dir=None)
    with owner_client(create_app(settings)) as c:
        yield c


def factory_of(client: TestClient) -> sessionmaker[Session]:
    return client.app.state.session_factory  # type: ignore[attr-defined, no-any-return]


def cents(text: str) -> int:
    whole, _, frac = text.partition(".")
    return int(whole) * 100 + (-int(frac) if whole.startswith("-") else int(frac))


def exact(minor: int, numerator: int, denominator: int) -> int:
    """``minor * numerator / denominator`` rounded half to even, with exact fractions."""
    return round(Fraction(minor * numerator, denominator))


def streams_of(client: TestClient) -> dict[str, dict[str, Any]]:
    return {s["owners"][0]["member_name"]: s for s in client.get("/api/income-streams").json()}


def payload(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "Sample side job",
        "type_key": "salary",
        "owners": [{"member_id": JORDAN}],
        "gross_annual": "20000.00",
        "take_home_annual": "18000.00",
    }
    return base | changes


def errors_of(res: Any) -> dict[str, str]:
    assert res.status_code == 422, res.text
    return {e["field"]: e["message"] for e in res.json()["errors"]}


def scenario_id(client: TestClient, name: str = "Likely") -> int:
    return next(s["id"] for s in client.get("/api/scenarios").json() if s["name"] == name)


def live(client: TestClient, name: str = "Likely") -> dict[str, Any]:
    res = client.get(
        f"/api/projection/scenarios/{scenario_id(client, name)}", params={"as_at": AS_AT}
    )
    assert res.status_code == 200, res.text
    return res.json()


def credited(row: dict[str, Any], category: str) -> int:
    return cents(next(c["credited"] for c in row["categories"] if c["category_key"] == category))


def delete_all_streams(client: TestClient) -> None:
    for stream in client.get("/api/income-streams").json():
        assert client.delete(f"/api/income-streams/{stream['id']}").status_code == 204


# --- the synthetic household ---------------------------------------------------------------


def test_the_synthetic_household_has_a_salary_for_each_member(sample: TestClient) -> None:
    streams = streams_of(sample)
    assert set(streams) == {"Alex Sample", "Jordan Sample"}
    alex = streams["Alex Sample"]
    assert (alex["type_key"], alex["tax_kind"], alex["ownership"]) == (
        "salary",
        "employment",
        "sole",
    )
    assert (alex["gross_annual"], alex["take_home_annual"]) == ("120000.00", "91000.00")
    assert (alex["start_on"], alex["start_event"]) == (None, None)  # paid from now
    assert alex["end_event"] == "retirement" and alex["credit_category"] == "cash"
    assert streams["Jordan Sample"]["take_home_annual"] == "66000.00"


# --- the service and API -------------------------------------------------------------------


def test_create_edit_and_delete_are_each_audited_with_the_stream_as_stored(
    sample: TestClient,
) -> None:
    made = sample.post("/api/income-streams", json=payload(notes="  Invented.  "))
    assert made.status_code == 201, made.text
    stream = made.json()
    assert (stream["notes"], stream["end_event"], stream["end_on"]) == (
        "Invented.",
        "retirement",
        None,
    )
    assert stream["type_label"] == "Salary" and stream["tax_kind"] == "employment"

    edited = sample.put(
        f"/api/income-streams/{stream['id']}",
        json=payload(notes="Invented.", take_home_annual="17500.50"),
    )
    assert edited.status_code == 200 and edited.json()["take_home_annual"] == "17500.50"
    assert sample.delete(f"/api/income-streams/{stream['id']}").status_code == 204
    assert sample.get(f"/api/income-streams/{stream['id']}").status_code == 404

    trail = sample.get(
        "/api/audit", params={"entity_type": "income_stream", "entity_id": stream["id"]}
    ).json()["items"]
    assert [(e["action"], e["summary"]) for e in trail] == [
        ("delete", "Deleted income stream 'Sample side job' (20000.00 a year gross)"),
        ("update", "Edited income stream 'Sample side job' (20000.00 a year gross)"),
        ("create", "Added income stream 'Sample side job' (20000.00 a year gross)"),
    ]
    assert trail[1]["changes"] == [
        {"field": "Take home annual", "before": "AUD 18000.00", "after": "AUD 17500.50"}
    ]


def test_an_edit_that_changes_nothing_writes_no_audit_event(sample: TestClient) -> None:
    stream = sample.post("/api/income-streams", json=payload()).json()
    assert sample.put(f"/api/income-streams/{stream['id']}", json=payload()).status_code == 200
    trail = sample.get(
        "/api/audit", params={"entity_type": "income_stream", "entity_id": stream["id"]}
    ).json()["items"]
    assert [e["action"] for e in trail] == ["create"]


@pytest.mark.parametrize(
    ("changes", "field", "message"),
    [
        ({"type_key": "business"}, "type_key", "not available"),
        ({"ownership": "joint"}, "ownership", "Joint income streams are not available"),
        ({"owners": []}, "owners", "exactly one Member"),
        ({"owners": [{"member_id": 1}, {"member_id": 2}]}, "owners", "exactly one Member"),
        ({"owners": [{"member_id": 99}]}, "owners", "does not exist"),
        ({"owners": [{"member_id": 1, "share_bp": 5000}]}, "owners", "holds 100%"),
        ({"name": "  "}, "name", "required"),
        ({"gross_annual": "0"}, "gross_annual", "above 0"),
        ({"gross_annual": "12.345"}, "gross_annual", "decimal places"),
        ({"gross_annual": ""}, "gross_annual", "Enter an amount"),
        ({"take_home_annual": None}, "take_home_annual", "Enter an amount"),
        ({"take_home_annual": "20000.01"}, "take_home_annual", "can't exceed gross"),
        ({"take_home_annual": "-1"}, "take_home_annual", "negative"),
        ({"start_on": "2027-01-01", "start_event": "retirement"}, "start_event", "not both"),
        ({"end_on": "2030-01-01", "end_event": "retirement"}, "end_event", "not both"),
        ({"end_event": "birthday"}, "end_event", "not a Life event"),
        ({"start_on": "2030-01-01", "end_on": "2029-01-01"}, "end_on", "after the start"),
        ({"credit_category": "property"}, "credit_category", "not projected"),
    ],
)
def test_invalid_streams_are_refused_with_a_field_error_and_nothing_is_written(
    sample: TestClient, changes: dict[str, Any], field: str, message: str
) -> None:
    res = sample.post("/api/income-streams", json=payload(**changes))
    assert message in errors_of(res)[field]
    assert len(sample.get("/api/income-streams").json()) == 2  # only the fixture's two


def test_every_problem_is_reported_together(sample: TestClient) -> None:
    res = sample.post(
        "/api/income-streams", json=payload(name="", gross_annual="x", type_key="business")
    )
    assert set(errors_of(res)) == {"name", "gross_annual", "type_key"}


def test_a_dated_stream_is_stored_as_stated_and_a_missing_stream_is_not_found(
    sample: TestClient,
) -> None:
    made = sample.post(
        "/api/income-streams",
        json=payload(start_on="2028-01-01", end_on="2032-06-30"),
    ).json()
    assert (made["start_on"], made["end_on"], made["end_event"]) == (
        "2028-01-01",
        "2032-06-30",
        None,
    )
    assert sample.get("/api/income-streams/9999").status_code == 404
    assert sample.put("/api/income-streams/9999", json=payload()).status_code == 404
    assert sample.delete("/api/income-streams/9999").status_code == 404


def test_a_member_who_owns_a_stream_cannot_be_deleted(sample: TestClient) -> None:
    sam = sample.post(
        "/api/household/members", json={"name": "Sam Sample", "date_of_birth": "1990-01-01"}
    ).json()["id"]
    stream = sample.post("/api/income-streams", json=payload(owners=[{"member_id": sam}])).json()
    res = sample.delete(f"/api/household/members/{sam}")
    assert res.status_code == 409 and "they own an income stream" in res.json()["message"]
    assert sample.delete(f"/api/income-streams/{stream['id']}").status_code == 204
    assert sample.delete(f"/api/household/members/{sam}").status_code == 204


# --- salary and employer super in the projection -------------------------------------------


def test_year_zero_credits_take_home_to_cash_and_employer_super_to_each_owners_line(
    sample: TestClient,
) -> None:
    out = live(sample)
    first = out["rows"][0]
    assert credited(first, "cash") == 9_100_000 + 6_600_000
    assert credited(first, "superannuation") == 1_224_000 + 867_000  # 12,240.00 + 8,670.00
    assert credited(first, "investments") == 0  # annual_saving is not applied to salaried Members


def test_each_salary_ends_in_its_owners_retirement_year(sample: TestClient) -> None:
    # Alex retires at 60 (2044-03-12: year 18) and Jordan at 62 (2048-09-30: year 22).
    rows = live(sample)["rows"]
    cash = [credited(r, "cash") for r in rows[:24]]
    assert cash[17] == 9_100_000 + 6_600_000  # the last year Alex works
    assert cash[18] == 6_600_000 and cash[21] == 6_600_000  # Jordan alone
    assert cash[22] == 0
    sup = [credited(r, "superannuation") for r in rows[:24]]
    assert sup[17] == 1_224_000 + 867_000 and sup[18] == 867_000 and sup[22] == 0


def test_real_wage_growth_is_one_scale_from_the_stored_base_and_a_member_value_beats_the_household(
    sample: TestClient,
) -> None:
    changed = sample.put(
        "/api/assumptions",
        json={
            "changes": [
                {"key": "real_wage_growth", "scope_type": "household", "value": "2"},
                {
                    "key": "real_wage_growth",
                    "scope_type": "member",
                    "scope_ref": str(ALEX),
                    "value": "3.5",
                },
            ]
        },
    )
    assert changed.status_code == 200, changed.text
    row = live(sample)["rows"][5]
    take_home = exact(9_100_000, 1_035_000**5, 10**30) + exact(6_600_000, 1_020_000**5, 10**30)
    assert credited(row, "cash") == take_home
    # Employer super on the grown gross, one rounding of the whole product.
    alex_super = exact(12_000_000 * 12 * 85, 1_035_000**5, 10**30 * 10_000)
    jordan_super = exact(8_500_000 * 12 * 85, 1_020_000**5, 10**30 * 10_000)
    assert credited(row, "superannuation") == alex_super + jordan_super


def test_a_salaried_member_without_a_real_wage_growth_is_a_named_missing_value(
    sample: TestClient,
) -> None:
    sample.put(
        "/api/assumptions",
        json={"changes": [{"key": "real_wage_growth", "scope_type": "household", "value": None}]},
    )
    res = sample.get(f"/api/projection/scenarios/{scenario_id(sample)}", params={"as_at": AS_AT})
    problems = errors_of(res)
    assert set(problems) == {f"real_wage_growth.{ALEX}", f"real_wage_growth.{JORDAN}"}
    assert (
        "Real wage growth per year (Alex Sample) has no value"
        in problems[f"real_wage_growth.{ALEX}"]
    )
    # A household with no salary needs no wage growth: nothing is demanded of it.
    delete_all_streams(sample)
    assert (
        sample.get(
            f"/api/projection/scenarios/{scenario_id(sample)}", params={"as_at": AS_AT}
        ).status_code
        == 200
    )


def test_the_provenance_lists_the_real_wage_growth_of_each_salaried_member(
    sample: TestClient,
) -> None:
    stated = [p for p in live(sample)["provenance"] if p["key"] == "real_wage_growth"]
    # Both Members use the Household's value, so it is listed once.
    assert [(p["scope_ref"], p["value"], p["layer"]) for p in stated] == [("", "0", "household")]
    delete_all_streams(sample)
    assert not [p for p in live(sample)["provenance"] if p["key"] == "real_wage_growth"]


def test_a_scenario_overriding_the_sg_rate_uses_it_and_says_so(sample: TestClient) -> None:
    sample.put(
        "/api/assumptions",
        json={"changes": [{"key": "rule.sg_rate", "scope_type": "household", "value": "11"}]},
    )
    out = live(sample)
    assert credited(out["rows"][0], "superannuation") == 1_224_000 // 12 * 11 + 867_000 // 12 * 11
    sg = next(r for r in out["income"]["rates"] if r["key"] == "sg_rate")
    assert (sg["value"], sg["layer"], sg["from_year"]) == ("11", "household", 0)


def test_the_rates_used_name_their_financial_year_and_source_layer(sample: TestClient) -> None:
    rates = {(r["key"], r["from_year"]): r for r in live(sample)["income"]["rates"]}
    assert rates[("sg_rate", 0)]["value"] == "12"
    assert rates[("sg_rate", 0)]["financial_year"] == "2026-27"
    assert rates[("contributions_tax_rate", 0)]["value"] == "15"
    assert {r["layer"] for r in rates.values()} == {"rule_pack"}
    assert len(rates) == 2  # constant from FY 2026-27 on: one span each


def test_a_date_before_the_pack_has_rates_is_a_clear_error_not_a_zero(sample: TestClient) -> None:
    res = sample.get(
        f"/api/projection/scenarios/{scenario_id(sample)}", params={"as_at": "2020-10-25"}
    )
    assert "Superannuation can't be projected from this date" in errors_of(res)["body"]


# --- the year-0 spending rule (ADR-0017 s5) -------------------------------------------------


def replace_phases(client: TestClient, phases: list[dict[str, Any]]) -> None:
    res = client.put("/api/spending-phases", json={"phases": phases})
    assert res.status_code == 200, res.text


def phase(amount: str, anchor: str = "last_retirement", offset: int = 0) -> dict[str, Any]:
    return {"anchor": anchor, "offset_years": offset, "annual_amount": amount}


def test_a_salary_in_force_in_year_zero_needs_a_phase_starting_at_the_projection_start(
    sample: TestClient,
) -> None:
    replace_phases(sample, [phase("70000.00")])
    res = sample.get(f"/api/projection/scenarios/{scenario_id(sample)}", params={"as_at": AS_AT})
    message = errors_of(res)["spending_phases"]
    assert message.startswith("Working-years spending has no value.")
    assert "Alex Sample and Jordan Sample earn salaries from" in message
    # A phase at the start of the projection satisfies it...
    replace_phases(sample, [phase("90000.00", "start"), phase("70000.00")])
    assert live(sample)["rows"][0]["need"] == "90000.00"
    # ...but one that starts a year in does not: year 0 would be silently free.
    replace_phases(sample, [phase("90000.00", "start", 1), phase("70000.00")])
    res = sample.get(f"/api/projection/scenarios/{scenario_id(sample)}", params={"as_at": AS_AT})
    assert "spending_phases" in errors_of(res)


def test_the_rule_holds_for_the_comparison_and_the_ad_hoc_run_too(sample: TestClient) -> None:
    replace_phases(sample, [phase("70000.00")])
    compared = sample.get("/api/projection/compare", params={"as_at": AS_AT}).json()
    likely = next(s for s in compared["scenarios"] if s["scenario"]["name"] == "Likely")
    assert not likely["resolved"]
    assert likely["errors"][0]["field"] == "spending_phases"
    # The Scenario with its own whole list (it carries a start phase) still resolves.
    early = next(
        s for s in compared["scenarios"] if s["scenario"]["name"].startswith("Retire early")
    )
    assert early["resolved"]
    body = {
        "as_at": AS_AT,
        "inflation": "2.5",
        "returns": [
            {"category": c, "rate": "5"}
            for c in ("cash", "investments", "superannuation", "overseas_pensions")
        ],
        "retirement_ages": [{"member_id": ALEX, "age": 60}, {"member_id": JORDAN, "age": 62}],
        "spending_phases": [phase("70000.00")],
        "annual_saving": None,
        "horizon_age": 95,
    }
    res = sample.post("/api/projection/run", json=body)
    assert "Working-years spending has no value" in errors_of(res)["spending_phases"]


def test_nobody_paid_in_year_zero_needs_no_working_years_phase(sample: TestClient) -> None:
    """Both Members have already retired by their own settings, so no salary is in force."""
    replace_phases(sample, [phase("70000.00")])
    sample.put(
        "/api/assumptions",
        json={
            "changes": [
                {
                    "key": "retirement_age",
                    "scope_type": "member",
                    "scope_ref": str(m),
                    "value": "30",
                }
                for m in (ALEX, JORDAN)
            ]
        },
    )
    out = live(sample)
    assert credited(out["rows"][0], "cash") == 0 and credited(out["rows"][0], "superannuation") == 0
    assert all(s["until_year"] == 0 for s in out["income"]["streams"])


def test_without_any_salary_the_projection_behaves_as_before(sample: TestClient) -> None:
    delete_all_streams(sample)
    replace_phases(sample, [phase("70000.00")])
    out = live(sample)
    assert out["income"] == {"streams": [], "rates": []}
    # annual_saving (20,000.00 in the synthetic household) splits equally again, as in B1.
    assert credited(out["rows"][0], "investments") == 2_000_000
    assert credited(out["rows"][0], "cash") == 0


def test_annual_saving_is_dropped_only_for_the_member_with_a_salary(sample: TestClient) -> None:
    jordan = streams_of(sample)["Jordan Sample"]
    assert sample.delete(f"/api/income-streams/{jordan['id']}").status_code == 204
    out = live(sample)
    # Jordan has no salary, so keeps the B1 equal share (10,000.00); Alex, salaried, gets none.
    investments = [
        cents(c["credited"])
        for c in out["rows"][0]["categories"]
        if c["category_key"] == "investments"
    ]
    assert investments == [1_000_000]


# --- saved runs keep what they used --------------------------------------------------------


def test_a_saved_run_keeps_the_stream_amounts_and_rates_it_used(sample: TestClient) -> None:
    saved = sample.post(
        "/api/projection/runs", json={"scenario_id": scenario_id(sample), "as_at": AS_AT}
    )
    assert saved.status_code == 201, saved.text
    run_id = saved.json()["id"]
    before = sample.get(f"/api/projection/runs/{run_id}").json()
    used = {s["name"]: s for s in before["income"]["streams"]}
    assert used["Alex salary (Sample Employer)"]["gross_annual"] == "120000.00"
    assert used["Alex salary (Sample Employer)"]["until_year"] == 18
    assert used["Alex salary (Sample Employer)"]["real_wage_growth"] == "0"
    assert {r["key"]: r["value"] for r in before["income"]["rates"]} == {
        "sg_rate": "12",
        "contributions_tax_rate": "15",
    }
    assert before["run"]["rule_pack_version"] == "au/2026-27/r7+1:5be77e"  # +1: fixture capture

    # Edit one stream, delete the other, and change the growth: the saved run does not move.
    streams = streams_of(sample)
    sample.put(
        f"/api/income-streams/{streams['Alex Sample']['id']}",
        json=payload(
            name="Alex salary (changed)",
            owners=[{"member_id": ALEX}],
            gross_annual="200000.00",
            take_home_annual="140000.00",
        ),
    )
    sample.delete(f"/api/income-streams/{streams['Jordan Sample']['id']}")
    sample.put(
        "/api/assumptions",
        json={"changes": [{"key": "real_wage_growth", "scope_type": "household", "value": "4"}]},
    )
    assert live(sample)["rows"] != before["rows"]  # the live projection moved...
    assert sample.get(f"/api/projection/runs/{run_id}").json() == before  # ...the saved run did not


# --- the service directly ------------------------------------------------------------------


def test_the_service_returns_streams_as_the_projection_uses_them(sample: TestClient) -> None:
    with factory_of(sample)() as session:
        plain = income_streams.projection_streams(session)
        assert {(s.member_id, s.gross_annual.amount_minor) for s in plain} == {
            (ALEX, 12_000_000),
            (JORDAN, 8_500_000),
        }
        assert income_streams.salary_member_ids(session) == {ALEX, JORDAN}
