"""Income reconciliation and the income statement Record type (ADR-0019 section 9).

Unit tests of the Record type and of the pure ``reconcile`` function, Hypothesis properties for the
shares, and integration tests through the API against a real temporary SQLite database holding the
synthetic household's invented income statements (FY 2025-26). Every figure is invented.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator, Mapping
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from finance360.capabilities.tax import labels, reconcile, record_types
from finance360.capabilities.tax.input import InputError
from finance360.capabilities.tax.record_types import income_statement
from finance360.capabilities.tax.record_types.base import Record
from finance360.config import Settings
from finance360.core.money import Money, parse_amount
from finance360.core.rate import PPM
from finance360.main import create_app
from support import TestClient, owner_client

REPO = Path(__file__).resolve().parents[2]
ALEX, JORDAN = 1, 2
FY = 2026  # FY 2025-26


def aud(text: str) -> Money:
    return parse_amount(text, "AUD")


def minor(text: str) -> dict[str, Any]:
    return {"minor": aud(text).amount_minor, "ccy": "AUD"}


def a_statement(record_id: int = 1, member: int = ALEX, **figures: str | None) -> Record:
    """A statement; pass ``net_pay=None`` for one that does not state net pay."""
    payload = {
        "gross_payments": minor(figures.pop("gross_payments", "100000.00") or "0"),
        "tax_withheld": minor(figures.pop("tax_withheld", "24000.00") or "0"),
        "employer_super": minor(figures.pop("employer_super", "12000.00") or "0"),
    }
    net_pay = figures.pop("net_pay", "76000.00")
    if net_pay is not None:
        payload["net_pay"] = minor(net_pay)
    payload.update({name: minor(text) for name, text in figures.items() if text is not None})
    return Record(
        record_id,
        member,
        "income_statement",
        date(2025, 7, 1),
        date(2026, 6, 30),
        income_statement.parse(payload),
    )


def totals_of(records: list[Record], member: int = ALEX) -> dict[str, Money]:
    found = record_types.label_totals(records, FY)
    return {
        label: total.total
        for (who, label), total in found.items()
        if who == member and isinstance(total.total, Money)
    }


# --- the Record type (pure) -------------------------------------------------------------------


def test_one_statement_feeds_return_labels_and_memo_labels() -> None:
    record = a_statement(3, reportable_fringe_benefits="9000.00", novated_lease_post_tax="6000.00")
    by_label = {c.label: c.value for c in record_types.contributions_of(record)}
    assert by_label["salary"] == aud("100000.00")
    assert by_label["tax_withheld"] == aud("24000.00")
    assert by_label["reportable_fringe_benefits"] == aud("9000.00")
    assert by_label["employer_super"] == aud("12000.00")
    assert by_label["novated_lease_post_tax"] == aud("6000.00")
    assert by_label["net_pay_received"] == aud("76000.00")
    assert {c.record_id for c in record_types.contributions_of(record)} == {3}
    memo = {k for k in by_label if labels.LABELS[k].direction == labels.MEMO}
    assert memo == {
        "employer_super",
        "novated_lease_post_tax",
        "payroll_other_deductions",
        "net_pay_received",
        "super_insurance_premiums",
    }


def test_a_statement_round_trips_through_its_payload() -> None:
    record = a_statement(super_insurance_premiums="600.00")
    again = income_statement.parse(income_statement.to_payload(record.value))
    assert again == record.value


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(net_pay={"minor": 1.5, "ccy": "AUD"}),
        lambda p: p.update(net_pay={"minor": -1, "ccy": "AUD"}),
        lambda p: p.update(net_pay={"minor": 1, "ccy": "USD"}),
        lambda p: p.update(surprise={"minor": 1, "ccy": "AUD"}),
    ],
)
def test_a_bad_statement_is_rejected(mutate: Any) -> None:
    payload = income_statement.to_payload(a_statement().value)
    mutate(payload)
    with pytest.raises(InputError):
        income_statement.parse(payload)


def test_net_pay_is_optional_and_not_stated_is_not_zero() -> None:
    record = a_statement(4, net_pay=None)
    assert record.value.net_pay is None
    assert "net_pay" not in income_statement.to_payload(record.value)  # not stated stays not stated
    assert income_statement.parse(income_statement.to_payload(record.value)) == record.value
    assert "net_pay_received" not in {c.label for c in record_types.contributions_of(record)}
    # a stated zero is a statement of nothing received, and still counts
    zero = a_statement(5, net_pay="0.00")
    by_label = {c.label: c.value for c in record_types.contributions_of(zero)}
    assert by_label["net_pay_received"] == aud("0.00")
    # an old record's payload (with net pay) parses unchanged
    old = a_statement(6, net_pay="76000.00")
    assert old.value.net_pay == aud("76000.00")


def test_comparing_years_treats_a_net_pay_not_stated_as_nothing_to_compare() -> None:
    now = a_statement(1, net_pay=None).value
    before = a_statement(2, net_pay="76000.00").value
    change = {c.field: c for c in income_statement.compare(now, before)}["net_pay"]
    assert (change.current, change.previous, change.change) == (
        aud("0.00"),
        aud("76000.00"),
        aud("-76000.00"),
    )


# --- reconcile (pure) -------------------------------------------------------------------------


def test_golden_a_package_with_a_small_gap() -> None:
    """Package 134,400.00 = 120,000.00 + 14,400.00. The slices add to 134,200.00."""
    records = [
        a_statement(
            gross_payments="120000.00",
            tax_withheld="28500.00",
            employer_super="14400.00",
            net_pay="84100.00",
            novated_lease_post_tax="6000.00",
            other_deductions="1200.00",
            reportable_fringe_benefits="9000.00",
            super_insurance_premiums="600.00",
        )
    ]
    result = reconcile.reconcile(totals_of(records), ALEX)
    assert result.package == aud("134400.00")
    assert result.unreconciled == aud("200.00")
    drawn = {s.key: s for s in result.slices}
    assert list(drawn) == ["tax", "super", "lease", "other", "take_home", "unreconciled"]
    assert drawn["take_home"].amount == aud("84100.00")
    assert sum(s.share_ppm or 0 for s in result.slices) == PPM
    # 28,500.00 of 134,400.00 is 21.2054% (to 4 places); the exact ppm is the allocation's
    assert reconcile.percent_text(drawn["tax"].share_ppm or 0) == "21.2054"
    # beside the pie and under it, never in it
    assert result.reportable_fringe_benefits == aud("9000.00")
    assert result.super_insurance_premiums == aud("600.00")
    assert "fringe" not in " ".join(drawn) and "insurance" not in " ".join(drawn)


def test_golden_net_pay_not_stated_takes_home_the_remainder_with_no_unreconciled() -> None:
    """Package 112,000.00. Take-home = gross 100,000.00 - tax 24,000.00 - lease 5,000.00 - other
    1,000.00 = 70,000.00 (the super is in both the package and its slice)."""
    record = a_statement(net_pay=None, novated_lease_post_tax="5000.00", other_deductions="1000.00")
    worked = income_statement.worked_out_take_home(record.value)
    assert worked == aud("70000.00")
    result = reconcile.reconcile(totals_of([record]), ALEX, worked)
    assert result.package == aud("112000.00")
    drawn = {s.key: s.amount for s in result.slices}
    assert drawn["take_home"] == aud("70000.00")
    assert result.take_home_worked_out == aud("70000.00")
    assert result.unreconciled == aud("0.00")
    assert "unreconciled" not in drawn
    assert sum(s.share_ppm or 0 for s in result.slices) == PPM


def test_golden_a_mixed_year_works_out_only_the_statement_that_does_not_state_net_pay() -> None:
    """Statement 1 states no net pay (worked out: 100,000.00 - 24,000.00 = 76,000.00). Statement 2
    states 40,500.00 against a package of 56,000.00 (gap 500.00 stays Unreconciled)."""
    first = a_statement(1, net_pay=None)
    second = a_statement(
        2, gross_payments="50000.00", tax_withheld="9000.00", employer_super="6000.00",
        net_pay="40500.00",
    )  # fmt: skip
    worked = income_statement.worked_out_take_home(first.value)
    result = reconcile.reconcile(totals_of([first, second]), ALEX, worked)
    assert worked == aud("76000.00")
    assert {s.key: s.amount for s in result.slices}["take_home"] == aud("116500.00")
    assert result.take_home_worked_out == aud("76000.00")
    assert result.unreconciled == aud("500.00")  # only the stated statement can leave a gap


def test_a_statement_whose_deductions_exceed_its_pay_works_out_nil_not_negative() -> None:
    record = a_statement(
        net_pay=None, gross_payments="10000.00", tax_withheld="9000.00", other_deductions="3000.00"
    )
    assert income_statement.worked_out_take_home(record.value) == aud("0.00")


def test_a_negative_unreconciled_is_signed_and_not_a_slice() -> None:
    records = [
        a_statement(
            gross_payments="85000.00",
            tax_withheld="19400.00",
            employer_super="10200.00",
            net_pay="66000.00",
        )
    ]
    result = reconcile.reconcile(totals_of(records), JORDAN)
    assert result.unreconciled == aud("-400.00")
    assert "unreconciled" not in {s.key for s in result.slices}
    assert result.unreconciled_share_ppm is None
    # the chart still divides exactly: no negative slice, no weight below zero
    assert all(s.amount.amount_minor >= 0 for s in result.slices)
    assert sum(s.share_ppm or 0 for s in result.slices) == PPM


def test_nothing_entered_is_empty_not_an_error() -> None:
    result = reconcile.reconcile({}, ALEX)
    assert result.package == aud("0.00") and result.unreconciled == aud("0.00")
    assert not result.has_figures
    assert all(s.share_ppm == 0 for s in result.slices)


def test_the_household_is_its_members_summed() -> None:
    a = totals_of([a_statement(1, ALEX)])
    b = totals_of([a_statement(2, JORDAN, gross_payments="50000.00", net_pay="26000.00")], JORDAN)
    household = reconcile.reconcile_household([a, b])
    assert household.member_id is None
    assert household.package == aud("100000.00") + aud("12000.00") + aud("50000.00") + aud(
        "12000.00"
    )
    assert household.unreconciled == aud("0.00")


def test_two_statements_in_a_year_add_up() -> None:
    first = a_statement(1, gross_payments="60000.00", net_pay="36000.00")
    second = a_statement(2, gross_payments="40000.00", net_pay="40000.00", tax_withheld="0.00")
    total = totals_of([first, second])
    assert total["salary"] == aud("100000.00")
    assert total["net_pay_received"] == aud("76000.00")


slice_amounts = st.integers(min_value=0, max_value=10**12)


@st.composite
def label_totals(draw: st.DrawFn) -> Mapping[str, Money]:
    names = (
        "salary",
        "employer_super",
        "tax_withheld",
        "novated_lease_post_tax",
        "payroll_other_deductions",
        "net_pay_received",
    )
    return {name: Money(draw(slice_amounts), "AUD") for name in names}


@settings(max_examples=300, deadline=None)
@given(label_totals())
def test_the_shares_always_sum_to_exactly_one_hundred_percent(totals: Mapping[str, Money]) -> None:
    """Whatever the figures (a gap, no gap, a negative gap, all zero), the drawn shares are whole
    parts per million summing to exactly 100%; with nothing to draw, all are zero."""
    result = reconcile.reconcile(totals)
    shares = [s.share_ppm for s in result.slices]
    assert all(isinstance(s, int) and 0 <= s <= PPM for s in shares)
    drawn = sum(s.amount.amount_minor for s in result.slices)
    assert sum(s or 0 for s in shares) == (PPM if drawn else 0)


@settings(max_examples=300, deadline=None)
@given(label_totals())
def test_unreconciled_is_the_package_minus_the_slices(totals: Mapping[str, Money]) -> None:
    result = reconcile.reconcile(totals)
    five = [s.amount.amount_minor for s in result.slices if s.key != "unreconciled"]
    assert result.package.amount_minor == (
        totals["salary"].amount_minor + totals["employer_super"].amount_minor
    )
    assert result.unreconciled.amount_minor == result.package.amount_minor - sum(five)
    drawn_gap = [s for s in result.slices if s.key == "unreconciled"]
    if result.unreconciled.amount_minor > 0:
        assert [s.amount for s in drawn_gap] == [result.unreconciled]
    else:
        assert drawn_gap == []  # a zero or negative gap is never a slice


# --- through the API --------------------------------------------------------------------------


@pytest.fixture
def sample(tmp_path: Path) -> Iterator[TestClient]:
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(REPO / "fixtures" / "sample_household" / "household.json", data / "household.json")
    app_settings = Settings(data_dir=data, port=8000, git_sha="dev", static_dir=None)
    with owner_client(create_app(app_settings)) as c:
        yield c


def reconciliation(client: TestClient, year: int = FY) -> dict[str, Any]:
    res = client.get("/api/tax/income-reconciliation", params={"financial_year": year})
    assert res.status_code == 200, res.text
    return res.json()


def test_the_synthetic_household_reconciles_each_member_and_the_household(
    sample: TestClient,
) -> None:
    data = reconciliation(sample)
    assert data["financial_year_label"] == "2025-26"
    alex, jordan = data["members"]
    assert (alex["name"], alex["package"], alex["unreconciled"]) == (
        "Alex Sample",
        "134400.00",
        "200.00",
    )
    assert (jordan["name"], jordan["package"], jordan["unreconciled"]) == (
        "Jordan Sample",
        "95200.00",
        "-400.00",
    )
    assert [s["key"] for s in alex["slices"]][-1] == "unreconciled"
    assert "unreconciled" not in [s["key"] for s in jordan["slices"]]
    assert alex["reportable_fringe_benefits"] == "9000.00"
    assert alex["super_insurance_premiums"] == "600.00"
    household = data["household"]
    assert household["name"] == "Household" and household["member_id"] is None
    assert household["package"] == "229600.00" and household["unreconciled"] == "-200.00"


def test_the_percentages_sum_to_exactly_100(sample: TestClient) -> None:
    from decimal import Decimal

    data = reconciliation(sample)
    for item in [*data["members"], data["household"]]:
        assert sum(Decimal(s["share_percent"]) for s in item["slices"]) == Decimal(100)


def test_a_year_without_statements_has_nothing_to_reconcile(sample: TestClient) -> None:
    data = reconciliation(sample, 2027)
    assert data["members"] == [] and data["household"] is None


def test_a_salary_entered_as_a_line_is_not_reconciled(sample: TestClient) -> None:
    sample.delete("/api/tax/records/1")  # Alex's income statement
    data = reconciliation(sample)
    assert [m["name"] for m in data["members"]] == ["Jordan Sample"]


def test_memo_figures_do_not_make_an_estimate_incomplete(sample: TestClient) -> None:
    res = sample.post(
        "/api/tax/estimate",
        json={"financial_year": FY, "dependants": 0, "member_ids": [ALEX, JORDAN]},
    )
    assert res.status_code == 200, res.text
    assert res.json()["incomplete"] is False and res.json()["unmodelled"] == []
    alex = res.json()["members"][0]
    # the statement's gross pay, the interest, and the business less the camera and the home office
    # and the car (6,000.00 - 495.89 - 1,037.40 - 849.55)
    assert alex["total_income"] == "235651.00"
    assert alex["credits"] == "28500.00"  # its tax withheld


def statement_body(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "member_id": ALEX,
        "record_type": "income_statement",
        "from_on": "2025-07-01",
        "to_on": "2026-06-30",
        "description": "Income statement, Invented Pty",
        "statement": {
            "gross_payments": "50000.00",
            "tax_withheld": "9000.00",
            "employer_super": "6000.00",
            "net_pay": "41000.00",
        },
    }
    body.update(changes)
    return body


def test_an_income_statement_can_be_added_edited_and_shows_its_figures(
    sample: TestClient,
) -> None:
    created = sample.post("/api/tax/records", json=statement_body())
    assert created.status_code == 201, created.text
    record = created.json()
    assert record["record_type"] == "income_statement"
    assert record["label_key"] == "salary" and record["amount"] == "50000.00"
    assert record["statement"]["net_pay"] == "41000.00"
    assert record["statement"]["novated_lease_post_tax"] == "0.00"
    body = statement_body()
    body["statement"]["net_pay"] = "40000.00"
    edited = sample.put(f"/api/tax/records/{record['id']}", json=body)
    assert edited.status_code == 200, edited.text
    assert edited.json()["statement"]["net_pay"] == "40000.00"
    audit = sample.get(
        "/api/audit", params={"entity_type": "tax_record", "entity_id": record["id"]}
    )
    assert audit.status_code == 200 and len(audit.json()["items"]) == 2


def test_through_the_api_net_pay_stated_not_stated_and_mixed(sample: TestClient) -> None:
    """FY 2026-27 has no statements in the synthetic household. Alex: one with no net pay, then one
    that states it with a 500.00 gap. Jordan: only a stated one."""
    year = 2027

    def add(member: int, description: str, **statement: str) -> None:
        body = statement_body(
            member_id=member,
            from_on="2026-07-01",
            to_on="2027-06-30",
            description=description,
            statement=statement,
        )
        res = sample.post("/api/tax/records", json=body)
        assert res.status_code == 201, res.text
        assert res.json()["statement"]["net_pay"] == statement.get("net_pay")

    add(ALEX, "Not stated", gross_payments="100000.00", tax_withheld="24000.00",
        employer_super="12000.00", novated_lease_post_tax="5000.00",
        other_deductions="1000.00")  # fmt: skip
    one = reconciliation(sample, year)
    alex = one["members"][0]
    take_home = next(s for s in alex["slices"] if s["key"] == "take_home")
    assert (take_home["amount"], alex["take_home_worked_out"]) == ("70000.00", "70000.00")
    assert alex["unreconciled"] == "0.00" and "unreconciled" not in [
        s["key"] for s in alex["slices"]
    ]

    add(ALEX, "Stated", gross_payments="50000.00", tax_withheld="9000.00",
        employer_super="6000.00", net_pay="40500.00")  # fmt: skip
    add(JORDAN, "Jordan stated", gross_payments="50000.00", tax_withheld="9000.00",
        employer_super="6000.00", net_pay="41000.00")  # fmt: skip
    mixed = reconciliation(sample, year)
    alex, jordan = mixed["members"]
    take_home = next(s for s in alex["slices"] if s["key"] == "take_home")
    assert take_home["amount"] == "110500.00"  # 70,000.00 worked out + 40,500.00 stated
    assert (alex["take_home_worked_out"], alex["unreconciled"]) == ("70000.00", "500.00")
    assert (jordan["take_home_worked_out"], jordan["unreconciled"]) == ("0.00", "0.00")
    household = mixed["household"]
    assert (household["take_home_worked_out"], household["unreconciled"]) == ("70000.00", "500.00")
    # existing records are unchanged: FY 2025-26 still states every net pay
    assert all(m["take_home_worked_out"] == "0.00" for m in reconciliation(sample)["members"])


def test_a_bad_statement_names_each_field(sample: TestClient) -> None:
    body = statement_body()
    body["statement"] = {"gross_payments": "-5", "tax_withheld": "abc", "net_pay": "1.00"}
    res = sample.post("/api/tax/records", json=body)
    assert res.status_code == 422
    fields = {e["field"] for e in res.json()["errors"]}
    assert fields == {"gross_payments", "tax_withheld", "employer_super"}
