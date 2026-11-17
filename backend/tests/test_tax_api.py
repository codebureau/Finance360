"""The tax estimate end to end: Rule pack -> rules, service, HTTP, real temporary SQLite.

The pack used for the figures is the shipped AU pack with its tax figures swapped for INVENTED ones
(``tax_support.invented_pack``) in a fictional year, so nothing here states an official figure.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from tax_support import FY, aud, invented_pack, invented_rules

from finance360.capabilities.tax import labels, pack_rules
from finance360.config import Settings
from finance360.core import rulepack
from finance360.core.models.audit import AuditEvent
from finance360.core.money import Money
from finance360.main import create_app
from finance360.rulepacks.au import PACK
from support import TestClient, owner_client, without_depreciating_assets

REPO = Path(__file__).resolve().parents[2]
DIV293_KEYS = ("tax.div293.threshold", "tax.div293.rate", "tax.super.concessional_cap")
ALEX, JORDAN = 1, 2


def swap_pack(pack: rulepack.RulePack) -> None:
    rulepack.unregister("au")
    rulepack.register(pack)


@pytest.fixture
def sample(tmp_path: Path) -> Iterator[TestClient]:
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(REPO / "fixtures" / "sample_household" / "household.json", data / "household.json")
    settings = Settings(data_dir=data, port=8000, git_sha="dev", static_dir=None)
    with owner_client(create_app(settings)) as c:
        yield without_depreciating_assets(c)


@pytest.fixture
def invented(sample: TestClient) -> Iterator[None]:
    """The invented pack, registered after the app (which registers the shipped one)."""
    assert sample is not None
    swap_pack(invented_pack())
    yield
    swap_pack(PACK)


def body(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"financial_year": FY, "dependants": 0, "member_ids": [ALEX, JORDAN]}
    base.update(changes)
    return base


def enter(client: TestClient, member_id: int, label: str, amount: str, **more: Any) -> int:
    """One Tax record in the fictional year FY (the whole year), through the API."""
    res = client.post(
        "/api/tax/records",
        json={
            "member_id": member_id,
            "record_type": "line",
            "label": label,
            "amount": amount,
            "from_on": f"{FY - 1}-07-01",
            "to_on": f"{FY}-06-30",
            "description": f"Invented {label}",
            **more,
        },
    )
    assert res.status_code == 201, res.text
    return int(res.json()["id"])


def enter_household(client: TestClient) -> None:
    for member, salary, withheld in (
        (ALEX, "80000.00", "14000.00"),
        (JORDAN, "28000.00", "1000.00"),
    ):
        enter(client, member, "salary", salary)
        enter(client, member, "tax_withheld", withheld)


# --- the pack -> rules ----------------------------------------------------------------------


def test_rules_are_built_from_the_pack_for_exactly_the_year_asked() -> None:
    assert pack_rules.build_rules(invented_pack(), FY) == invented_rules()
    assert pack_rules.missing_keys(invented_pack(), FY) == ()
    assert pack_rules.tax_years(invented_pack()) == (FY,)


def test_a_year_without_figures_fails_naming_the_year_and_never_borrows_another() -> None:
    pack = invented_pack()
    with pytest.raises(pack_rules.TaxRulesError) as error:
        pack_rules.build_rules(pack, FY + 1)
    message = str(error.value)
    assert f"FY {rulepack.fy_label(FY + 1)}" in message
    assert "never uses another year's" in message
    assert f"Capture them on Rule figures (FY {rulepack.fy_label(FY + 1)})." in message
    assert "rulepack_update" not in message


def test_a_partly_captured_year_fails_listing_what_is_missing() -> None:
    pack = invented_pack(drop=("tax.help.t1", "tax.sbito.cap"))
    with pytest.raises(pack_rules.TaxRulesError) as error:
        pack_rules.build_rules(pack, FY)
    assert "tax.help.t1, tax.sbito.cap" in str(error.value)


def test_the_year_may_have_no_instant_deduction() -> None:
    rules = pack_rules.build_rules(invented_pack(instant=None), FY)
    assert rules.standard_work_deduction is None and rules == invented_rules(instant=None)


def test_the_year_may_have_no_foreign_offset_cap() -> None:
    rules = pack_rules.build_rules(invented_pack(drop=(pack_rules.FOREIGN_OFFSET_CAP_KEY,)), FY)
    assert rules.foreign_offset_cap is None
    assert pack_rules.build_rules(invented_pack(), FY).foreign_offset_cap == aud("75")


def test_provisional_markers_come_from_the_pack() -> None:
    pack = invented_pack(provisional={"tax.help.t1": "announced, not yet law"})
    rules = pack_rules.build_rules(pack, FY)
    assert rules.provisional == (("tax.help.t1", "announced, not yet law"),)
    assert rules == invented_rules(provisional=(("tax.help.t1", "announced, not yet law"),))


def test_the_shipped_pack_can_estimate_exactly_the_captured_years() -> None:
    assert pack_rules.tax_years(PACK) == (2026,)
    assert pack_rules.missing_keys(PACK, 2026) == ()
    rules = pack_rules.build_rules(PACK, 2026)
    assert {key for key, _ in rules.provisional} == {
        "tax.medicare.low_income.single_lower",
        "tax.medicare.low_income.family_lower",
        "tax.medicare.low_income.family_per_child",
        *DIV293_KEYS,  # provisional until confirmed on Rule figures
        pack_rules.FOREIGN_OFFSET_CAP_KEY,
    }
    assert rules.foreign_offset_cap == Money(100_000, "AUD")
    with pytest.raises(pack_rules.TaxRulesError):
        pack_rules.build_rules(PACK, 2025)


def test_every_figure_an_estimate_needs_is_declared_for_capture() -> None:
    from finance360.rulepacks.au.tax_specs import SPECS

    declared = {s.key for s in SPECS}
    needed = (
        set(pack_rules.REQUIRED.values())
        | {pack_rules.OPTIONAL_KEY, pack_rules.FOREIGN_OFFSET_CAP_KEY}
        | set(pack_rules.RECORD_KEYS)  # a Record type reads it; only a record using it needs it
        | set(DIV293_KEYS)  # the Division 293 working reads them; an estimate does not
    )
    assert needed == declared


# --- the setup -------------------------------------------------------------------------------


def test_the_setup_lists_every_label_modelled_or_not(sample: TestClient) -> None:
    res = sample.get("/api/tax/setup")
    assert res.status_code == 200
    data = res.json()
    assert [label["key"] for label in data["labels"]] == [
        label.key for label in labels.all_labels()
    ]
    assert any(not label["modelled"] for label in data["labels"])
    assert [s["key"] for s in data["sections"]] == [k for k, _ in labels.SECTIONS]
    assert {label["section"] for label in data["labels"]} <= {s["key"] for s in data["sections"]}


def test_with_no_captured_year_the_setup_says_so(sample: TestClient) -> None:
    swap_pack(
        invented_pack(
            drop=(*pack_rules.REQUIRED.values(), pack_rules.FOREIGN_OFFSET_CAP_KEY), instant=None
        )
    )
    try:
        data = sample.get("/api/tax/setup").json()
    finally:
        swap_pack(PACK)
    assert data["years"] == []


def test_the_setup_lists_the_shipped_pack_s_captured_year(sample: TestClient) -> None:
    data = sample.get("/api/tax/setup").json()
    # the sample household's one invented captured figure (FY 2026-27 car rate) adds "+1:<digest>"
    assert data["rule_pack_version"].startswith(f"{PACK.version_label}+1:")
    held = {y["financial_year"]: y["complete"] for y in data["years"]}
    assert held[2026] is True


@pytest.mark.usefixtures("invented")
def test_the_setup_lists_the_years_the_pack_can_estimate(sample: TestClient) -> None:
    data = sample.get("/api/tax/setup").json()
    assert data["years"] == [
        {
            "financial_year": FY,
            "label": rulepack.fy_label(FY),
            "complete": True,
            "missing": [],
            "provisional": [],
        }
    ]


def test_a_partly_captured_year_is_listed_as_incomplete(sample: TestClient) -> None:
    swap_pack(invented_pack(drop=("tax.help.t1",)))
    try:
        (year,) = sample.get("/api/tax/setup").json()["years"]
    finally:
        swap_pack(PACK)
    assert year["complete"] is False and year["missing"] == ["tax.help.t1"]


# --- the estimate ------------------------------------------------------------------------------


@pytest.mark.usefixtures("invented")
def test_an_estimate_for_the_synthetic_household(sample: TestClient) -> None:
    enter_household(sample)
    res = sample.post("/api/tax/estimate", json=body())
    assert res.status_code == 200, res.text
    data = res.json()
    assert (data["financial_year"], data["financial_year_label"]) == (FY, rulepack.fy_label(FY))
    assert data["engine_version"] == 5
    assert data["rule_pack_version"] == f"au/2096-97/r{PACK.revision}"
    assert data["incomplete"] is False and data["unmodelled"] == []
    alex, jordan = data["members"]
    assert (alex["name"], alex["taxable_income"], alex["tax_on_taxable_income"]) == (
        "Alex Sample",
        "80000.00",
        "10610.00",
    )
    assert (alex["medicare_levy"], alex["total_tax"], alex["net_outcome"]) == (
        "2400.00",
        "13010.00",
        "990.00",
    )
    assert (jordan["offsets"], jordan["medicare_levy"], jordan["net_outcome"]) == (
        "450.00",
        "720.00",
        "-390.00",
    )
    assert data["household"]["net_outcome"] == "600.00"
    assert (data["household"]["medicare_levy"], data["household"]["offsets"]) == (
        "3120.00",
        "450.00",
    )
    assert data["household"]["surcharge_thresholds"] == "family"


@pytest.mark.usefixtures("invented")
def test_an_unmodelled_figure_is_listed_and_marks_the_estimate_incomplete(
    sample: TestClient,
) -> None:
    enter_household(sample)
    enter(sample, JORDAN, "government_allowances", "500.00")
    data = sample.post("/api/tax/estimate", json=body()).json()
    assert data["incomplete"] is True
    assert data["unmodelled"] == [
        {
            "member_id": JORDAN,
            "name": "Jordan Sample",
            "label_key": "government_allowances",
            "return_item": "5",
            "label_name": "Australian Government allowances and payments",
            "amount": "500.00",
        }
    ]
    # the figure changes no tax: it is reported, not modelled
    assert data["household"]["total_tax"] == "14400.00"


def test_a_provisional_figure_surfaces_as_a_warning_on_the_estimate(sample: TestClient) -> None:
    swap_pack(invented_pack(provisional={"tax.help.t1": "announced, not yet law"}))
    try:
        data = sample.post("/api/tax/estimate", json=body()).json()
        year = sample.get("/api/tax/setup").json()["years"][0]
    finally:
        swap_pack(PACK)
    assert data["provisional"] == [{"key": "tax.help.t1", "note": "announced, not yet law"}]
    assert year["provisional"] == data["provisional"]


def test_a_year_with_no_figures_is_a_422_naming_the_year_and_the_fix(sample: TestClient) -> None:
    res = sample.post("/api/tax/estimate", json=body(financial_year=2025))
    assert res.status_code == 422
    (error,) = res.json()["errors"]
    assert error["field"] == "financial_year"
    assert "FY 2024-25" in error["message"]
    assert "Capture them on Rule figures (FY 2024-25)." in error["message"]


@pytest.mark.usefixtures("invented")
def test_a_neighbouring_year_is_not_used_instead(sample: TestClient) -> None:
    for year in (FY - 1, FY + 1):
        res = sample.post("/api/tax/estimate", json=body(financial_year=year))
        assert res.status_code == 422, year
        assert rulepack.fy_label(year) in res.json()["errors"][0]["message"]


@pytest.mark.usefixtures("invented")
def test_days_that_add_up_beyond_the_year_are_a_422_not_a_crash(sample: TestClient) -> None:
    for day in ("07-01", "10-01"):  # two facts of 200 days each, 400 in a 365-day year
        res = sample.post(
            "/api/tax/records",
            json={
                "member_id": ALEX,
                "record_type": "fact",
                "label": "surcharge_exempt_days",
                "value": 200,
                "from_on": f"{FY - 1}-{day}",
                "description": "Invented cover days",
            },
        )
        assert res.status_code == 201, res.text
    res = sample.post("/api/tax/estimate", json=body())
    assert res.status_code == 422
    assert "surcharge_exempt_days" in res.json()["errors"][0]["message"]


@pytest.mark.usefixtures("invented")
@pytest.mark.parametrize(
    "changes",
    [
        {"member_ids": []},
        {"member_ids": [99]},
        {"member_ids": [ALEX, ALEX]},
        {"dependants": -1},
        {"financial_year": 12},
    ],
)
def test_a_bad_request_is_a_422_in_the_shared_error_shape(
    sample: TestClient, changes: dict[str, Any]
) -> None:
    res = sample.post("/api/tax/estimate", json=body(**changes))
    assert res.status_code == 422
    assert all(set(e) == {"field", "message"} for e in res.json()["errors"])


@pytest.mark.usefixtures("invented")
def test_one_member_is_assessed_alone_from_their_own_records(sample: TestClient) -> None:
    enter_household(sample)
    data = sample.post("/api/tax/estimate", json=body(member_ids=[ALEX])).json()
    assert [m["name"] for m in data["members"]] == ["Alex Sample"]
    assert data["members"][0]["taxable_income"] == "80000.00"  # not Jordan's 28,000.00 as well
    assert data["household"]["surcharge_thresholds"] == "single"


@pytest.mark.usefixtures("invented")
def test_a_record_outside_the_year_is_not_in_the_estimate(sample: TestClient) -> None:
    enter(sample, ALEX, "salary", "80000.00")
    next_year = sample.post(
        "/api/tax/records",
        json={
            "member_id": ALEX,
            "label": "salary",
            "amount": "9999.00",
            "from_on": f"{FY}-07-01",  # the first day of the next year
            "description": "Invented, next year",
        },
    )
    assert next_year.status_code == 201
    data = sample.post("/api/tax/estimate", json=body(member_ids=[ALEX])).json()
    assert data["members"][0]["taxable_income"] == "80000.00"


@pytest.mark.usefixtures("invented")
def test_nothing_is_stored_by_an_estimate(sample: TestClient) -> None:
    factory = sample.app.state.session_factory  # type: ignore[attr-defined]

    def audit_count() -> int:
        with factory() as session:
            assert isinstance(session, Session)
            return session.scalar(select(func.count()).select_from(AuditEvent)) or 0

    before = audit_count()
    assert sample.post("/api/tax/estimate", json=body()).status_code == 200
    assert audit_count() == before
    assert aud("0")  # amounts stay exact strings; nothing here is a float
