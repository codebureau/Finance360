"""Tax into the projection (ADR-0019 sections 1 and 10).

Unit golden tests over the pure estimate (the synthetic AU pack's published figures, hand-checked),
carry-forward in ``core.rulepack``, and integration over the synthetic household in a real
temporary SQLite database: the ``tax_in_projection`` and ``div293_payer`` Assumptions, tax off
leaving a run exactly as it was, a saved run recording the tax engine and Rule pack versions, and
Division 293 paid a year late.

Hand-checked answers (FY 2025-26 figures in the pack: brackets 0/16%/30%/37%/45% from 0, 18,200,
45,000, 135,000 and 190,000; Medicare levy 2%; Division 293 threshold 250,000.00 at 15%; the
concessional cap 30,000.00; SG 12%):

* Gross 100,000.00: tax on taxable income 26,800 x 16% + 55,000 x 30% = 4,288 + 16,500 = 20,788.00
  (no low income offset left at that income), Medicare levy 2,000.00, no surcharge (the single
  threshold is 101,000.00). Total 22,788.00, so take-home 77,212.00.
* Gross 300,000.00: employer super 36,000.00, of which 6,000.00 is over the cap, so the low tax
  contributions are 30,000.00. Division 293 income 300,000 + 30,000 = 330,000, 80,000 over the
  threshold, and the lesser of 30,000 and 80,000 is 30,000, so Division 293 is 4,500.00.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from finance360.capabilities.projection import assumptions, income, tax_income
from finance360.capabilities.projection.input import LineDebit, Outflow
from finance360.config import Settings
from finance360.core import rulepack
from finance360.core.models.projection_run import ProjectionRun
from finance360.core.money import Money
from finance360.core.rate import Rate
from finance360.core.rulepack import Param
from finance360.main import create_app
from finance360.rulepacks.au import PACK
from support import TestClient, owner_client

REPO = Path(__file__).resolve().parents[2]
AS_AT = "2026-10-25"
ALEX, JORDAN = 1, 2


def aud(minor: int) -> Money:
    return Money(minor, "AUD")


# --- carry-forward (ADR-0018 section 3) ----------------------------------------------------


def test_a_frozen_threshold_carried_two_years_falls_by_inflation_in_one_rounding() -> None:
    found = rulepack.carried_value(PACK, "tax.lito.max", 2028, Rate.from_percent("3"))
    assert found is not None
    # 700.00 x 1e6^2 / 1.03e6^2 = 65,982.2... rounded once to 65,982 minor units.
    assert (found.value, found.source_fy, found.behind, found.policy) == (
        aud(65_982),
        2026,
        2,
        rulepack.FROZEN,
    )


def test_a_cpi_figure_is_repeated_and_a_published_year_is_not_carried() -> None:
    held = rulepack.carried_value(PACK, "tax.mls.rate_tier1", 2030, Rate.from_percent("3"))
    exact = rulepack.carried_value(PACK, "tax.lito.max", 2026, Rate.from_percent("3"))
    assert held is not None and exact is not None
    assert held.value == Rate.from_percent("1") and held.behind == 4
    assert exact.value == aud(70_000) and exact.behind == 0


def test_a_year_before_any_figure_has_none() -> None:
    assert rulepack.carried_value(PACK, "tax.lito.max", 2020, Rate.zero()) is None


# --- the estimate, pure --------------------------------------------------------------------


def stream(stream_id: int, member_id: int, gross: int) -> income.Stream:
    return income.Stream(
        stream_id, "Sample salary", "salary", member_id, aud(gross), aud(1), "cash"
    )


def run_estimate(
    gross_minor: int,
    *,
    years: int,
    payer: str = assumptions.PAYER_OWN,
    inflation: str = "0",
) -> tax_income.Estimated:
    sg = Rate.from_percent("12")
    return tax_income.estimate(
        pack=PACK,
        first_fy=2026,
        years=years,
        inflation=Rate.from_percent(inflation),
        members=[(ALEX, date(1984, 3, 12))],
        streams=[stream(10, ALEX, gross_minor)],
        spans=[income.Span(0, None)],
        growth={ALEX: Rate.zero()},
        rates=income.RatesFor([sg] * years, [Rate.from_percent("15")] * years),
        payers={ALEX: tax_income.PayerUsed(ALEX, payer, "default")},
    )


def test_take_home_is_gross_less_the_engines_total_tax() -> None:
    est = run_estimate(10_000_000, years=1)
    # 100,000.00 gross: 20,788.00 income tax + 2,000.00 Medicare levy = 22,788.00.
    assert est.take_home[10][0] == aud(7_721_200)
    year = est.used.years[0]
    assert (year.members[0].gross, year.members[0].tax) == (aud(10_000_000), aud(2_278_800))
    assert not year.carried and year.source_fy == 2026


def test_division_293_is_paid_one_year_after_the_year_it_is_for() -> None:
    est = run_estimate(30_000_000, years=3)
    pays = [(p.for_fy, p.paid_year, p.estimated_from_year_0) for p in est.used.payments]
    # FY 2025-26 (year 0) is paid in year 1; year 0 also pays the year just ended, estimated
    # from year-0 figures; the last year's falls after the horizon and is not paid.
    assert pays == [
        (2025, 0, True),
        (2026, 1, False),
        (2027, 2, False),
        (2028, None, False),
    ]
    assert all(p.amount == aud(450_000) for p in est.used.payments)  # 4,500.00 each
    assert est.used.outflows() == (
        Outflow(aud(450_000), 0, 1),
        Outflow(aud(450_000), 1, 2),
        Outflow(aud(450_000), 2, 3),
    )


def test_the_super_payer_becomes_line_debits_in_the_same_years_as_own_outflows() -> None:
    own = run_estimate(30_000_000, years=3)
    sup = run_estimate(30_000_000, years=3, payer=assumptions.PAYER_SUPER)
    # The lag is decided once: both payers pay each liability in the same projection year.
    key = [(p.member_id, p.for_fy, p.paid_year, p.estimated_from_year_0) for p in own.used.payments]
    assert key == [
        (p.member_id, p.for_fy, p.paid_year, p.estimated_from_year_0) for p in sup.used.payments
    ]
    assert all(p.modelled for p in sup.used.payments)
    assert (
        [d.debit.year for d in sup.used.debits()]
        == [o.from_year for o in own.used.outflows()]
        == [0, 1, 2]
    )
    # Each payer produces only its own kind of flow.
    assert sup.used.outflows() == () and own.used.debits() == ()
    first = sup.used.debits()[0]
    assert first.debit == LineDebit(
        ALEX,
        "superannuation",
        0,
        aud(450_000),
        "div293",
        "FY2024-25, estimated from year-0 figures",
    )
    assert (first.for_fy, first.estimated_from_year_0, first.payer_layer) == (2025, True, "default")
    assert first.rule_pack_version == PACK.version_label
    assert sup.used.debits()[1].debit.ref == "FY2025-26"  # not the stand-in
    # The last year's liability falls after the horizon: no debit, listed with no payment year.
    assert sup.used.payments[-1].paid_year is None and len(sup.used.debits()) == 3


def test_changing_the_lag_moves_both_payers_together(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tax_income, "LAG_YEARS", 2)
    own = run_estimate(30_000_000, years=4)
    sup = run_estimate(30_000_000, years=4, payer=assumptions.PAYER_SUPER)
    assert [d.debit.year for d in sup.used.debits()] == [o.from_year for o in own.used.outflows()]
    # The stand-in for the year just ended is now paid in year 1, and year 0's in year 2.
    assert [d.debit.year for d in sup.used.debits()][:2] == [1, 2]


def test_years_past_the_packs_last_figures_are_carried_and_say_so() -> None:
    est = run_estimate(10_000_000, years=3, inflation="3")
    assert [(y.fy, y.source_fy, y.carried) for y in est.used.years] == [
        (2026, 2026, False),
        (2027, 2026, True),
        (2028, 2026, True),
    ]
    assert est.used.years[0].frozen_eroded == 0 and est.used.years[2].frozen_eroded > 0


def test_a_run_records_the_tax_engine_and_rule_pack_versions() -> None:
    used = run_estimate(10_000_000, years=1).used
    assert used.tax_engine_version == 5  # TAX_ENGINE_VERSION
    assert used.rule_pack_version == PACK.version_label


def test_a_pack_with_no_tax_figures_at_all_is_refused_naming_them() -> None:
    bare = rulepack.RulePack(
        "t", "xx", 1, (Param("x", rulepack.INT, rulepack.CPI, "invented"),), {2026: {"x": 1}}
    )
    with pytest.raises(tax_income.TaxIncomeError, match="no tax figures"):
        tax_income.estimate(
            pack=bare,
            first_fy=2026,
            years=1,
            inflation=Rate.zero(),
            members=[(ALEX, date(1984, 3, 12))],
            streams=[stream(10, ALEX, 10_000_000)],
            spans=[income.Span(0, None)],
            growth={ALEX: Rate.zero()},
            rates=None,
            payers={},
        )


# --- Assumptions ---------------------------------------------------------------------------


def test_the_choice_keys_parse_only_their_words() -> None:
    assert assumptions.parse_value("tax_in_projection", " ON ") == "on"
    assert assumptions.parse_value("div293_payer", "own") == "own"
    with pytest.raises(assumptions.AssumptionValueError, match="Choose one of: off, on"):
        assumptions.parse_value("tax_in_projection", "maybe")


# --- the synthetic household, end to end ---------------------------------------------------


@pytest.fixture
def sample(tmp_path: Path) -> Iterator[TestClient]:
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(REPO / "fixtures" / "sample_household" / "household.json", data / "household.json")
    settings = Settings(data_dir=data, port=8000, git_sha="dev", static_dir=None)
    with owner_client(create_app(settings)) as c:
        yield c


def scenario_id(client: TestClient, name: str) -> int:
    return next(s["id"] for s in client.get("/api/scenarios").json() if s["name"] == name)


def state(client: TestClient, scenario: int, *changes: tuple[str, str, str, str | None]) -> Any:
    body = {
        "scenario_id": scenario,
        "changes": [
            {"key": k, "scope_type": t, "scope_ref": r, "value": v} for k, t, r, v in changes
        ],
    }
    return client.put("/api/assumptions", json=body)


def new_scenario(client: TestClient, name: str) -> int:
    res = client.post(
        "/api/scenarios", json={"name": name, "copy_from": scenario_id(client, "Likely")}
    )
    assert res.status_code == 201, res.text
    return int(res.json()["id"])


def project(client: TestClient, scenario: int) -> dict[str, Any]:
    res = client.get(f"/api/projection/scenarios/{scenario}", params={"as_at": AS_AT})
    assert res.status_code == 200, res.text
    return res.json()


def test_tax_off_is_the_default_and_leaves_a_run_exactly_as_it_was(sample: TestClient) -> None:
    likely = scenario_id(sample, "Likely")
    before = project(sample, likely)
    assert before["tax"] is None
    # Stating "off" is the same as not stating it, in every figure.
    other = new_scenario(sample, "Tax stated off")
    assert state(sample, other, ("tax_in_projection", "household", "", "off")).status_code == 200
    after = project(sample, other)
    assert after["tax"] is None
    same = {k: v for k, v in after.items() if k not in ("scenario", "provenance")}
    assert same == {k: v for k, v in before.items() if k not in ("scenario", "provenance")}


def test_tax_on_estimates_take_home_instead_of_the_stated_amount(sample: TestClient) -> None:
    on = new_scenario(sample, "Tax on")
    assert state(sample, on, ("tax_in_projection", "household", "", "on")).status_code == 200
    off = project(sample, scenario_id(sample, "Likely"))
    p = project(sample, on)
    tax = p["tax"]
    assert tax["tax_engine_version"] == 5 and tax["rule_pack_version"].startswith(
        PACK.version_label
    )
    year0 = tax["years"][0]
    assert year0["financial_year"] == "2026-27"
    # The pack's tax figures end at FY 2025-26, so even year 0 carries them forward, and says so.
    assert year0["carried"] and year0["source_financial_year"] == "2025-26"
    gross = {m["member_id"]: m for m in year0["members"]}
    assert gross[ALEX]["gross"] == "120000.00"
    # The stated take-home pay was 91,000.00; the engine's estimate is its own figure.
    assert gross[ALEX]["take_home"] != "91000.00"
    assert p["rows"][0]["categories"][0]["credited"] != off["rows"][0]["categories"][0]["credited"]


def test_a_saved_run_keeps_the_tax_versions_and_never_changes(sample: TestClient) -> None:
    on = new_scenario(sample, "Tax on")
    assert state(sample, on, ("tax_in_projection", "household", "", "on")).status_code == 200
    res = sample.post("/api/projection/runs", json={"scenario_id": on, "as_at": AS_AT})
    assert res.status_code == 201, res.text
    run_id = res.json()["id"]
    saved = sample.get(f"/api/projection/runs/{run_id}").json()
    assert saved["tax"]["tax_engine_version"] == 5
    assert saved["tax"]["rule_pack_version"].startswith(PACK.version_label)
    assert res.json()["rule_pack_version"].startswith(PACK.version_label)
    live = project(sample, on)
    assert saved["tax"] == live["tax"] and saved["rows"] == live["rows"]


def with_division_293(client: TestClient, name: str, payer: str | None) -> dict[str, Any]:
    """A Scenario where Alex's pay outgrows inflation until Division 293 applies, tax on."""
    scenario = new_scenario(client, name)
    changes: list[tuple[str, str, str, str | None]] = [
        ("tax_in_projection", "household", "", "on"),
        ("real_wage_growth", "member", str(ALEX), "6"),
    ]
    if payer is not None:
        changes.append(("div293_payer", "member", str(ALEX), payer))
    assert state(client, scenario, *changes).status_code == 200
    return project(client, scenario)


def test_division_293_is_an_outflow_a_year_after_when_the_payer_is_own(sample: TestClient) -> None:
    own = with_division_293(sample, "Own payer", "own")
    pays = [p for p in own["tax"]["payments"] if p["member_id"] == ALEX]
    assert pays, "Alex's pay is high enough in the later working years for Division 293"
    for pay in pays:
        assert pay["payer"] == "own" and pay["modelled"]
        if not pay["estimated_from_year_0"] and pay["paid_year"] is not None:
            # Paid in the year after the one it is for.
            year = next(
                y for y in own["tax"]["years"] if y["financial_year"] == pay["for_financial_year"]
            )
            assert pay["paid_year"] == year["t"] + 1
    assert own["tax"]["payers"][0]["layer"] == "scenario"
    assert all(r["debits"] == [] for r in own["rows"])  # the household paid: nothing debited
    # A year with a payment needs more than the spending alone.
    paid_years = {p["paid_year"] for p in pays if p["paid_year"] is not None}
    base = project(sample, scenario_id(sample, "Likely"))
    assert all(own["rows"][t]["need"] != base["rows"][t]["need"] for t in paid_years), (
        "Division 293 is added to the year's need"
    )


def test_an_unstated_payer_is_super_and_is_the_same_as_stating_it(sample: TestClient) -> None:
    default = with_division_293(sample, "Default payer", None)
    sup = with_division_293(sample, "Super payer", "super")
    alex = {"member_id": ALEX, "payer": "super"}
    assert [p for p in default["tax"]["payers"] if p["member_id"] == ALEX] == [
        alex | {"layer": "default"}
    ]
    assert [p for p in sup["tax"]["payers"] if p["member_id"] == ALEX] == [
        alex | {"layer": "scenario"}
    ]
    assert default["tax"]["payments"] == sup["tax"]["payments"]
    assert default["rows"] == sup["rows"] and default["sustainable"] == sup["sustainable"]


def test_the_super_payer_debits_the_members_own_super_a_year_later(sample: TestClient) -> None:
    own = with_division_293(sample, "Own payer", "own")
    sup = with_division_293(sample, "Super payer", "super")
    base = project(sample, scenario_id(sample, "Likely"))
    pays = [p for p in sup["tax"]["payments"] if p["member_id"] == ALEX]
    paid = [p for p in pays if p["paid_year"] is not None]
    assert paid and all(p["modelled"] and p["payer"] == "super" for p in pays)
    debits = {t: row["debits"] for t, row in enumerate(sup["rows"]) if row["debits"]}
    assert sorted(debits) == sorted(p["paid_year"] for p in paid)
    for pay in paid:
        (d,) = [x for x in debits[pay["paid_year"]] if x["member_id"] == ALEX]
        assert (d["category_key"], d["reason"], d["requested"]) == (
            "superannuation",
            "div293",
            pay["amount"],
        )
        assert d["ref"] == f"FY{pay['for_financial_year']}" + (
            ", estimated from year-0 figures" if pay["estimated_from_year_0"] else ""
        )
        # The balance is large enough here: all of it comes from super, none from the household.
        assert d["paid"] == d["requested"] and d["unpaid"] == "0.00"
        row = sup["rows"][pay["paid_year"]]
        assert (
            row["paid_personally"] == "0.00"
            and row["need"] == base["rows"][pay["paid_year"]]["need"]
        )
        assert not any(r["debits"] for r in own["rows"])
        own_super = next(
            c
            for c in own["rows"][pay["paid_year"]]["categories"]
            if c["category_key"] == "superannuation"
        )
        sup_super = next(c for c in row["categories"] if c["category_key"] == "superannuation")
        assert sup_super["debited"] == pay["amount"] and own_super["debited"] == "0.00"
        assert float(sup_super["closing"]) < float(own_super["closing"])


def test_a_saved_run_records_each_debit_with_its_source(sample: TestClient) -> None:
    scenario = new_scenario(sample, "Super payer")
    changes = [
        ("tax_in_projection", "household", "", "on"),
        ("real_wage_growth", "member", str(ALEX), "6"),
        ("div293_payer", "member", str(ALEX), "super"),
    ]
    assert state(sample, scenario, *changes).status_code == 200
    res = sample.post("/api/projection/runs", json={"scenario_id": scenario, "as_at": AS_AT})
    assert res.status_code == 201, res.text
    with sample.app.state.session_factory() as session:  # type: ignore[attr-defined]
        row = session.scalars(select(ProjectionRun)).one()
    stored = json.loads(row.provenance_json)["debits"]
    saved = sample.get(f"/api/projection/runs/{res.json()['id']}").json()
    paid = [p for p in saved["tax"]["payments"] if p["paid_year"] is not None]
    assert len(stored) == len(paid) > 0
    first = stored[0]
    assert (first["member_id"], first["category_key"], first["reason"], first["payer_layer"]) == (
        ALEX,
        "superannuation",
        "div293",
        "scenario",
    )
    assert first["rule_pack_version"].startswith(PACK.version_label)
    # In the order they are paid, each with the financial year it is for.
    for entry, pay in zip(stored, paid, strict=True):
        assert (entry["year"], entry["estimated_from_year_0"]) == (
            pay["paid_year"],
            pay["estimated_from_year_0"],
        )
        assert f"FY{pay['for_financial_year']}" in entry["ref"]
        row = saved["rows"][entry["year"]]["debits"]
        assert f"{entry['amount']['minor'] / 100:.2f}" in [d["requested"] for d in row]
    assert saved["engine_version"] == 2 and saved["input_version"] == 2


def test_an_unknown_payer_is_refused(sample: TestClient) -> None:
    scenario = new_scenario(sample, "Bad payer")
    res = state(sample, scenario, ("div293_payer", "member", str(ALEX), "fund"))
    assert res.status_code == 422
    assert "Choose one of: own, super" in res.text
