"""Division 293 on the Tax estimate: sources, fallback and the working, end to end.

The synthetic household and the shipped AU pack for FY 2025-26: Alex's income is lifted above the
threshold by an invented consulting record, with a Super statement stating 14,400.00 of concessional
contributions; Jordan has no Super statement for the year, so her Income statement (employer
super 10,200.00 plus reportable employer super 1,800.00) is the source, and her Income stream the
fallback once that statement is removed.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from finance360.config import Settings
from finance360.main import create_app
from support import TestClient, owner_client

REPO = Path(__file__).resolve().parents[2]
FY = 2026
ALEX, JORDAN = 1, 2


@pytest.fixture
def sample(tmp_path: Path) -> Iterator[TestClient]:
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(REPO / "fixtures" / "sample_household" / "household.json", data / "household.json")
    settings = Settings(data_dir=data, port=8000, git_sha="dev", static_dir=None)
    with owner_client(create_app(settings)) as c:
        yield c


def estimate(client: TestClient) -> dict[str, Any]:
    res = client.post(
        "/api/tax/estimate",
        json={"financial_year": FY, "dependants": 0, "member_ids": [ALEX, JORDAN]},
    )
    assert res.status_code == 200, res.text
    return dict(res.json()["division_293"])


def member(data: dict[str, Any], member_id: int) -> dict[str, Any]:
    return next(m for m in data["members"] if m["member_id"] == member_id)


def steps(m: dict[str, Any]) -> dict[str, str]:
    return {s["title"]: s["result"]["value"] for s in m["steps"]}


def test_a_member_above_the_threshold_is_taxed_on_the_lesser_amount(sample: TestClient) -> None:
    alex = member(estimate(sample), ALEX)
    # Taxable 234,601 + fringe benefits 9,000 + no losses + 14,400 contributions = 258,001: 8,001
    # over the 250,000 threshold, which is less than the contributions, so 15% of 8,000.
    assert steps(alex) == {
        "Excess concessional contributions": "0.00",
        "Low tax contributions": "14400.00",
        "Division 293 income": "258001.00",
        "Income over the threshold": "8001.00",
        "Amount taxed": "8001.00",
        "Division 293 tax": "1200.15",
    }
    assert (alex["liability"], alex["applies"]) == ("1200.15", True)


def test_every_input_names_its_source(sample: TestClient) -> None:
    alex = member(estimate(sample), ALEX)
    by_label = {i["label"]: i for i in alex["inputs"]}
    assert by_label["Taxable income"]["amount"] == "234601.00"
    assert by_label["Reportable fringe benefits"]["amount"] == "9000.00"
    assert by_label["Division 293 threshold"]["amount"] == "250000.00"
    assert by_label["Concessional contributions cap"]["amount"] == "30000.00"
    contribution = by_label["Concessional contributions received: Sample Super Fund"]
    assert contribution["amount"] == "14400.00"
    assert contribution["source"] == "Super statement for Sample Super Fund, FY 2025-26"
    assert "tax.div293.threshold" in by_label["Division 293 threshold"]["source"]
    assert alex["contributions_source"] == "statement"


def test_the_rule_pack_figures_are_traced_and_provisional_until_confirmed(
    sample: TestClient,
) -> None:
    figures = {f["key"]: f for f in estimate(sample)["figures_used"]}
    assert set(figures) == {
        "tax.div293.threshold",
        "tax.div293.rate",
        "tax.super.concessional_cap",
    }
    assert figures["tax.div293.rate"]["value"] == "15%"
    assert all(f["provisional"] and f["source"] for f in figures.values())


def jordan_statement_ids(client: TestClient) -> list[int]:
    res = client.get("/api/tax/records", params={"member_id": JORDAN, "financial_year": FY})
    return [r["id"] for r in res.json() if r["record_type"] == "income_statement"]


def test_income_statements_are_the_source_when_there_is_no_super_statement_figure(
    sample: TestClient,
) -> None:
    jordan = member(estimate(sample), JORDAN)
    assert jordan["contributions_source"] == "income_statement"
    assert jordan["received"] == "12000.00"  # employer super 10,200.00 + reportable 1,800.00
    [line] = [i for i in jordan["inputs"] if i["label"].startswith("Income statement:")]
    assert line["label"] == (
        "Income statement: Sample Employer, employer super + reportable employer super"
    )
    assert line["amount"] == "12000.00"
    assert "employer super 10200.00 + reportable employer super 1800.00" in line["source"]
    assert any("by period worked, not by date received" in n for n in jordan["notes"])
    assert any("not on the Income statement" in n for n in jordan["notes"])
    assert steps(jordan)["Low tax contributions"] == "12000.00"
    assert jordan["liability"] == "0.00" and jordan["applies"] is False
    assert jordan["released"] is None  # no Super statement states it


def test_each_income_statement_is_its_own_line_and_they_add(sample: TestClient) -> None:
    res = sample.post(
        "/api/tax/records",
        json={
            "member_id": JORDAN,
            "record_type": "income_statement",
            "from_on": "2025-07-01",
            "to_on": "2026-06-30",
            "description": "Income statement, Second Employer",
            "statement": {
                "gross_payments": "5000.00",
                "tax_withheld": "500.00",
                "employer_super": "600.00",
                "reportable_employer_super": "250.50",
                "net_pay": "4500.00",
            },
        },
    )
    assert res.status_code == 201, res.text
    jordan = member(estimate(sample), JORDAN)
    lines = [i for i in jordan["inputs"] if i["label"].startswith("Income statement:")]
    assert sorted(i["amount"] for i in lines) == ["12000.00", "850.50"]
    assert jordan["received"] == "12850.50"
    assert jordan["contributions_source"] == "income_statement"


def test_an_income_statement_for_another_year_is_not_counted(sample: TestClient) -> None:
    # Jordan's FY 2023-24 statement (employer super 9,600.00) is on the register, not in this year.
    jordan = member(estimate(sample), JORDAN)
    assert jordan["received"] == "12000.00"


def test_the_income_stream_is_the_source_when_there_is_no_statement_of_either_kind(
    sample: TestClient,
) -> None:
    for record_id in jordan_statement_ids(sample):
        assert sample.delete(f"/api/tax/records/{record_id}").status_code == 204
    jordan = member(estimate(sample), JORDAN)
    assert jordan["contributions_source"] == "income_stream"
    assert jordan["received"] == "10200.00"  # gross 85,000.00 x 12%
    line = next(i for i in jordan["inputs"] if i["label"].startswith("Employer super"))
    assert "gross 85000.00" in line["source"] and "12%" in line["source"]
    assert jordan["liability"] == "0.00" and jordan["applies"] is False
    assert jordan["released"] is None  # no statement states it


def restate(client: TestClient, **changes: str | None) -> None:
    """Correct Alex's FY 2025-26 Super statement (the original stays, superseded)."""
    [account] = [a for a in client.get("/api/accounts").json() if a["name"] == "Sample Super Fund"]
    [statement] = [
        s
        for s in client.get(f"/api/accounts/{account['id']}/super-statements").json()
        if s["financial_year"] == FY
    ]
    fields = (
        "period_start period_end opening contributions_in investment_returns withdrawals closing "
        "contributions_tax fees_before_rebate rebate fees_inside_investment "
        "division_293_released insurance_premiums non_concessional_received concessional_received"
    ).split()
    body: dict[str, Any] = {k: statement[k] for k in fields}
    body["options"] = []
    body.update(changes)
    res = client.put(f"/api/super-statements/{statement['id']}", json=body)
    assert res.status_code == 200, res.text


def test_a_super_statement_figure_wins_and_income_statements_are_not_added(
    sample: TestClient,
) -> None:
    # Alex's Super statement states 14,400.00; his Income statement also holds 14,400.00 of
    # employer super. Adding them would double count: only the fund's figure is used.
    alex = member(estimate(sample), ALEX)
    assert alex["contributions_source"] == "statement"
    assert alex["received"] == "14400.00"
    assert not [i for i in alex["inputs"] if i["label"].startswith("Income statement:")]
    assert not any("by period worked" in n for n in alex["notes"])
    restate(sample, concessional_received="20000.00")
    alex = member(estimate(sample), ALEX)
    assert alex["received"] == "20000.00"  # not 34,400.00
    assert alex["contributions_source"] == "statement"


def test_a_statement_that_does_not_state_contributions_falls_back_and_says_so(
    sample: TestClient,
) -> None:
    restate(sample, concessional_received=None)
    alex = member(estimate(sample), ALEX)
    # Alex's Income statement (employer super 14,400.00, no reportable employer super) is next.
    assert alex["contributions_source"] == "income_statement"
    assert alex["received"] == "14400.00"
    assert any("does not state concessional" in n for n in alex["notes"])
    assert any("Income statement" in i["source"] for i in alex["inputs"])


def test_the_income_stream_follows_when_the_super_statement_and_income_statements_are_silent(
    sample: TestClient,
) -> None:
    restate(sample, concessional_received=None)
    res = sample.get("/api/tax/records", params={"member_id": ALEX, "financial_year": FY})
    for r in res.json():
        if r["record_type"] == "income_statement":
            assert sample.delete(f"/api/tax/records/{r['id']}").status_code == 204
    alex = member(estimate(sample), ALEX)
    assert alex["contributions_source"] == "income_stream"
    assert alex["received"] == "14400.00"  # gross 120,000.00 x 12%
    assert any("Income stream" in i["source"] for i in alex["inputs"])


def test_released_from_super_is_shown_from_the_statement(sample: TestClient) -> None:
    alex = member(estimate(sample), ALEX)
    assert alex["released"] == "0.00"
    assert alex["released_source"] == "Super statement: Sample Super Fund"


def test_the_cap_check_is_quiet_well_under_the_cap(sample: TestClient) -> None:
    alex = member(estimate(sample), ALEX)  # 14,400.00 against 30,000.00
    assert (alex["cap_status"], alex["cap_warning"]) == ("ok", None)


def test_the_cap_check_warns_within_ten_percent(sample: TestClient) -> None:
    restate(sample, concessional_received="28000.00")  # 93% of the cap
    alex = member(estimate(sample), ALEX)
    assert alex["cap_status"] == "near"
    assert alex["cap_warning"] is not None and "within 10%" in alex["cap_warning"]
    assert steps(alex)["Excess concessional contributions"] == "0.00"


def test_over_the_cap_warns_and_the_excess_leaves_the_low_tax_contributions(
    sample: TestClient,
) -> None:
    restate(sample, concessional_received="33000.00")
    alex = member(estimate(sample), ALEX)
    assert alex["cap_status"] == "over"
    assert alex["cap_warning"] is not None and "3000.00" in alex["cap_warning"]
    # 3,000.00 excess; 30,000.00 low tax contributions; Division 293 income 273,600.00, 23,600.00
    # over the threshold, less than the contributions, so 15% of 23,600.00.
    assert steps(alex) == {
        "Excess concessional contributions": "3000.00",
        "Low tax contributions": "30000.00",
        "Division 293 income": "273601.00",
        "Income over the threshold": "23601.00",
        "Amount taxed": "23601.00",
        "Division 293 tax": "3540.15",
    }
