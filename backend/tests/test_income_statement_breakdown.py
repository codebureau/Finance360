"""The income statement's ATO breakdown and "compared with last year".

Unit tests of the new fields, the labels they feed and the comparison maths; Hypothesis for the
comparison's exactness; integration tests through the API against a real temporary SQLite database
holding the synthetic household (Alex has FY 2024-25 and FY 2025-26 statements for the same
employer; Jordan's FY 2025-26 statement has no previous year). Every figure is invented.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from tax_support import FY, aud, invented_rules, tax_input

from finance360.capabilities.tax import engine, labels, record_types
from finance360.capabilities.tax.input import InputError, MemberInput
from finance360.capabilities.tax.record_types import income_statement
from finance360.capabilities.tax.record_types.base import Record
from finance360.config import Settings
from finance360.core.money import Money
from finance360.main import create_app
from support import TestClient, owner_client

REPO = Path(__file__).resolve().parents[2]
ALEX, JORDAN = 1, 2

REQUIRED = {"gross_payments": "100000.00", "tax_withheld": "24000.00",
            "employer_super": "12000.00", "net_pay": "76000.00"}  # fmt: skip


def money(text: str) -> dict[str, Any]:
    return {"minor": aud(text).amount_minor, "ccy": "AUD"}


def payload(**figures: Any) -> dict[str, Any]:
    body: dict[str, Any] = {k: money(v) for k, v in REQUIRED.items()}
    for name, value in figures.items():
        body[name] = (
            value
            if isinstance(value, str) and name in income_statement.TEXT_FIELDS
            else (money(value))
        )
    return body


def record(record_id: int = 1, /, **figures: Any) -> Record:
    return Record(
        record_id,
        ALEX,
        "income_statement",
        date(FY - 1, 7, 1),
        date(FY, 6, 30),
        income_statement.parse(payload(**figures)),
    )


def contributed(rec: Record) -> dict[str, list[Money]]:
    out: dict[str, list[Money]] = {}
    for c in record_types.contributions_of(rec):
        assert isinstance(c.value, Money)
        out.setdefault(c.label, []).append(c.value)
    return out


# --- parse -----------------------------------------------------------------------------------

NEW_MONEY = [
    "gross_amount",
    "bonuses_commissions",
    "paid_leave_amount",
    "salary_sacrifice_amount",
    "lump_sum_a",
    "lump_sum_b",
    "lump_sum_d",
    "lump_sum_e",
    "lump_sum_w",
    "allowances",
    "deductions_total",
]


@pytest.mark.parametrize("name", NEW_MONEY)
def test_every_new_money_field_parses_and_round_trips(name: str) -> None:
    value = income_statement.parse(payload(**{name: "123.45"}))
    assert getattr(value, name) == aud("123.45")
    assert income_statement.parse(income_statement.to_payload(value)) == value


@pytest.mark.parametrize("name", NEW_MONEY)
def test_a_new_money_field_must_be_aud_and_not_negative(name: str) -> None:
    bads = [{"minor": 100, "ccy": "USD"}]
    if name not in income_statement.NEGATIVE_OK:
        bads.append({"minor": -1, "ccy": "AUD"})
    for bad in bads:
        with pytest.raises(InputError):
            income_statement.parse({**payload(), name: bad})
    with pytest.raises(InputError):
        income_statement.parse({**payload(), name: {"minor": 1.5, "ccy": "AUD"}})


def test_only_the_salary_sacrifice_amount_may_be_negative_as_the_ato_prints_it() -> None:
    assert income_statement.NEGATIVE_OK == ("salary_sacrifice_amount",)
    value = income_statement.parse(payload(salary_sacrifice_amount="-3000.00"))
    assert value.salary_sacrifice_amount == aud("-3000.00")
    assert income_statement.parse(income_statement.to_payload(value)) == value
    with pytest.raises(InputError):
        income_statement.parse(payload(bonuses_commissions="-1.00"))
    # Information only: a negative figure feeds no Return label, so the totals are unchanged.
    assert contributed(record(salary_sacrifice_amount="-3000.00")) == contributed(record())


def test_the_text_facts_are_kept_trimmed_and_capped() -> None:
    value = income_statement.parse(
        payload(employer=" Sample Employer ", salary_sacrifice_type="Novated lease")
    )
    assert (value.employer, value.salary_sacrifice_type, value.leave_payment_type) == (
        "Sample Employer",
        "Novated lease",
        "",
    )
    assert income_statement.parse(income_statement.to_payload(value)) == value
    with pytest.raises(InputError):
        income_statement.parse({**payload(), "employer": "x" * (income_statement.TEXT_MAX + 1)})
    with pytest.raises(InputError):
        income_statement.parse({**payload(), "leave_payment_type": 5})


def test_an_unknown_key_is_rejected() -> None:
    with pytest.raises(InputError, match="no 'lump_sum_z'"):
        income_statement.parse({**payload(), "lump_sum_z": money("1.00")})


def test_the_taxable_fringe_benefit_part_cannot_exceed_the_total() -> None:
    income_statement.parse(
        payload(reportable_fringe_benefits="900.00", reportable_fringe_benefits_taxable="900.00")
    )
    with pytest.raises(InputError, match="cannot be more than"):
        income_statement.parse(
            payload(
                reportable_fringe_benefits="900.00", reportable_fringe_benefits_taxable="900.01"
            )
        )
    with pytest.raises(InputError):  # only the part entered: the total is what the return reads
        income_statement.parse(payload(reportable_fringe_benefits_taxable="1.00"))


def test_a_statement_stored_before_the_breakdown_still_parses() -> None:
    old = {
        **payload(),
        "reportable_fringe_benefits": money("0.00"),
        "reportable_employer_super": money("0.00"),
        "novated_lease_post_tax": money("0.00"),
        "other_deductions": money("0.00"),
        "super_insurance_premiums": money("600.00"),
    }
    value = income_statement.parse(old)
    assert value.super_insurance_premiums == aud("600.00")
    assert (value.lump_sum_a, value.allowances, value.employer) == (aud("0"), aud("0"), "")


# --- the label each field feeds ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "label"),
    [
        ("allowances", "allowances"),
        ("lump_sum_a", "employer_lump_sum"),
        ("lump_sum_b", "employer_lump_sum"),
        ("lump_sum_e", "lump_sum_arrears"),
        ("lump_sum_w", "return_to_work_payments"),
    ],
)
def test_each_figure_feeds_its_label(name: str, label: str) -> None:
    assert contributed(record(**{name: "250.00"}))[label] == [aud("250.00")]
    assert labels.LABELS[label].record_types.count("income_statement") == 1


@pytest.mark.parametrize(
    "name",
    [
        "gross_amount",
        "bonuses_commissions",
        "paid_leave_amount",
        "salary_sacrifice_amount",
        "lump_sum_d",
        "deductions_total",
        "reportable_fringe_benefits_taxable",
    ],
)
def test_an_information_only_figure_feeds_no_label(name: str) -> None:
    extra = {"reportable_fringe_benefits": "900.00"} if "fringe" in name else {}
    with_figure = contributed(record(**{name: "250.00"}, **extra))
    without = contributed(record(**extra))
    assert with_figure == without


def test_a_statement_without_the_new_figures_contributes_exactly_what_it_did_before() -> None:
    assert set(contributed(record())) == {
        "salary",
        "tax_withheld",
        "reportable_fringe_benefits",
        "reportable_employer_super",
        "employer_super",
        "novated_lease_post_tax",
        "payroll_other_deductions",
        "net_pay_received",
        "super_insurance_premiums",
    }


def test_the_breakdown_changes_no_reconciliation_label() -> None:
    plain = record_types.label_totals([record()], FY)
    full = record_types.label_totals(
        [
            record(
                gross_amount="90000.00",
                bonuses_commissions="10000.00",
                salary_sacrifice_amount="5000.00",
                salary_sacrifice_type="Novated lease",
                lump_sum_d="700.00",
                deductions_total="300.00",
            )
        ],
        FY,
    )
    assert {k: v.total for k, v in plain.items()} == {k: v.total for k, v in full.items()}


# --- the estimate ------------------------------------------------------------------------------


def estimate_of(**figures: Any) -> engine.TaxResult:
    totals = record_types.label_totals([record(**figures)], FY)
    member = MemberInput(
        ALEX, date(1980, 1, 1), tuple((label, t.total) for (_, label), t in totals.items())
    )
    return engine.estimate(tax_input(member, rules=invented_rules(FY)))


@pytest.mark.parametrize(
    ("name", "label"),
    [
        ("lump_sum_a", "employer_lump_sum"),
        ("lump_sum_b", "employer_lump_sum"),
        ("lump_sum_e", "lump_sum_arrears"),
        ("lump_sum_w", "return_to_work_payments"),
    ],
)
def test_a_lump_sum_marks_the_estimate_incomplete(name: str, label: str) -> None:
    result = estimate_of(**{name: "500.00"})
    assert result.incomplete
    assert [(u.label_key, u.value) for u in result.unmodelled] == [(label, aud("500.00"))]


def test_a_tax_free_lump_sum_d_and_the_breakdown_leave_the_estimate_complete() -> None:
    baseline = estimate_of()
    result = estimate_of(
        lump_sum_d="700.00", bonuses_commissions="9000.00", deductions_total="1.00"
    )
    assert not result.incomplete and result.unmodelled == ()
    assert result.members[0].total_income == baseline.members[0].total_income


def test_allowances_count_as_income_in_the_estimate() -> None:
    baseline = estimate_of().members[0]
    with_allowances = estimate_of(allowances="1500.00").members[0]
    assert with_allowances.total_income - baseline.total_income == aud("1500.00")


# --- compared with last year (pure) ------------------------------------------------------------


def values(**figures: str) -> income_statement.IncomeStatement:
    return income_statement.parse(payload(**figures))


def test_compare_gives_a_signed_exact_change_for_every_money_figure() -> None:
    this_year = values(bonuses_commissions="12000.00", salary_sacrifice_amount="5000.00")
    last_year = values(bonuses_commissions="8000.00", salary_sacrifice_amount="7000.50")
    by_figure = {c.field: c for c in income_statement.compare(this_year, last_year)}
    assert [c.field for c in income_statement.compare(this_year, last_year)] == list(
        income_statement.MONEY_FIELDS
    )
    bonus = by_figure["bonuses_commissions"]
    assert (bonus.current, bonus.previous, bonus.change) == (
        aud("12000.00"),
        aud("8000.00"),
        aud("4000.00"),
    )
    assert by_figure["salary_sacrifice_amount"].change == aud("-2000.50")
    assert by_figure["gross_payments"].change == aud("0.00")
    assert "employer" not in by_figure  # the text facts are not compared


@given(
    st.integers(min_value=0, max_value=10**12),
    st.integers(min_value=0, max_value=10**12),
)
@settings(max_examples=60, deadline=None)
def test_compare_change_is_exactly_current_minus_previous(now: int, before: int) -> None:
    def statement(minor: int) -> income_statement.IncomeStatement:
        return income_statement.parse(
            {**payload(), "bonuses_commissions": {"minor": minor, "ccy": "AUD"}}
        )

    (change,) = [
        c
        for c in income_statement.compare(statement(now), statement(before))
        if c.field == "bonuses_commissions"
    ]
    assert change.change.amount_minor == now - before
    assert change.previous.amount_minor + change.change.amount_minor == change.current.amount_minor


def test_the_employer_is_matched_by_its_name_or_else_its_description() -> None:
    key = income_statement.match_key
    assert key(" Sample  Employer", "Statement A") == key("sample employer", "Statement B")
    assert key("", "Income statement, Sample Employer") == key(
        "", "income statement,  sample employer"
    )
    assert key("Other Pty", "Same") != key("Sample Employer", "Same")
    assert key("", "Same") != key("Sample Employer", "Same")  # a name and no name do not match


def test_a_statement_belongs_to_the_year_its_last_day_falls_in() -> None:
    of = income_statement.financial_year_of_span
    assert of(date(2025, 7, 1), date(2026, 6, 30)) == 2026
    assert of(date(2025, 6, 30), date(2025, 6, 30)) == 2025
    assert of(date(2025, 7, 1), None) == 2026


# --- through the API ----------------------------------------------------------------------------


@pytest.fixture
def sample(tmp_path: Path) -> Iterator[TestClient]:
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(REPO / "fixtures" / "sample_household" / "household.json", data / "household.json")
    settings = Settings(data_dir=data, port=8000, git_sha="dev", static_dir=None)
    with owner_client(create_app(settings)) as c:
        yield c


def statements(client: TestClient, member: int) -> dict[int, dict[str, Any]]:
    """The member's statements by financial year (the year their last day falls in)."""
    res = client.get("/api/tax/records", params={"member_id": member})
    assert res.status_code == 200, res.text
    return {
        int(r["to_on"][:4]) + (1 if r["to_on"][5:7] >= "07" else 0): r
        for r in res.json()
        if r["record_type"] == "income_statement"
    }


def opened(client: TestClient, record_id: int) -> dict[str, Any]:
    res = client.get(f"/api/tax/records/{record_id}")
    assert res.status_code == 200, res.text
    return res.json()


def a_statement_body(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "member_id": ALEX,
        "record_type": "income_statement",
        "from_on": "2025-07-01",
        "to_on": "2026-06-30",
        "description": "Invented statement",
        "statement": {
            "gross_payments": "1000.00",
            "tax_withheld": "100.00",
            "employer_super": "120.00",
            "net_pay": "900.00",
        },
    }
    base.update(changes)
    return base


def test_the_statement_is_compared_with_last_years_for_the_same_employer(
    sample: TestClient,
) -> None:
    alex = statements(sample, ALEX)
    page = opened(sample, alex[2026]["id"])
    previous = page["previous_year"]
    assert (previous["record_id"], previous["financial_year"]) == (alex[2025]["id"], 2025)
    assert previous["financial_year_label"] == "2024-25"
    by_figure = {c["figure"]: c for c in previous["changes"]}
    assert by_figure["bonuses_commissions"] == {
        "figure": "bonuses_commissions",
        "current": "12000.00",
        "previous": "8000.00",
        "change": "4000.00",
    }
    assert by_figure["salary_sacrifice_amount"]["change"] == "2000.00"
    assert by_figure["gross_payments"]["change"] == "8000.00"
    assert by_figure["lump_sum_a"] == {
        "figure": "lump_sum_a",
        "current": "0.00",
        "previous": "0.00",
        "change": "0.00",
    }
    # last year's own page compares with nothing: there is no statement before it
    assert opened(sample, alex[2025]["id"])["previous_year"] is None


def test_a_fall_is_a_negative_change(sample: TestClient) -> None:
    alex = statements(sample, ALEX)
    # the other way round: FY 2024-25 against a later, bigger year is the same pair reversed
    sample.put(
        f"/api/tax/records/{alex[2025]['id']}",
        json={
            "member_id": ALEX,
            "record_type": "income_statement",
            "from_on": "2024-07-01",
            "to_on": "2025-06-30",
            "description": "Income statement, Sample Employer (FY 2024-25)",
            "statement": {
                "employer": "Sample Employer",
                "gross_payments": "130000.00",
                "tax_withheld": "25800.00",
                "employer_super": "13440.00",
                "net_pay": "80200.00",
            },
        },
    )
    page = opened(sample, alex[2026]["id"])
    by_figure = {c["figure"]: c for c in page["previous_year"]["changes"]}
    assert by_figure["gross_payments"]["change"] == "-10000.00"


def test_there_is_no_comparison_without_a_previous_statement(sample: TestClient) -> None:
    jordan = statements(sample, JORDAN)
    # Jordan has FY 2023-24 and FY 2025-26, but nothing for FY 2024-25
    assert opened(sample, jordan[2026]["id"])["previous_year"] is None
    # and the register list never carries a comparison
    assert all(r["previous_year"] is None for r in sample.get("/api/tax/records").json())


def test_a_different_employer_or_member_is_not_compared(sample: TestClient) -> None:
    created = sample.post(
        "/api/tax/records",
        json=a_statement_body(
            description="Other job",
            statement={**a_statement_body()["statement"], "employer": "Other Pty"},
        ),
    )
    assert created.status_code == 201, created.text
    assert opened(sample, created.json()["id"])["previous_year"] is None
    # Jordan's FY 2025-26 statement is not compared with Alex's FY 2024-25 one
    # (same description-less employer text, different Member)
    jordan = statements(sample, JORDAN)
    assert opened(sample, jordan[2026]["id"])["previous_year"] is None


def test_statements_without_an_employer_are_matched_by_description(sample: TestClient) -> None:
    last = sample.post(
        "/api/tax/records",
        json=a_statement_body(
            member_id=JORDAN,
            description="Jordan wages",
            from_on="2024-07-01",
            to_on="2025-06-30",
        ),
    )
    assert last.status_code == 201, last.text
    jordan = statements(sample, JORDAN)
    # the FY 2025-26 statement has another description, so it is not the same employer
    assert opened(sample, jordan[2026]["id"])["previous_year"] is None
    this = sample.post(
        "/api/tax/records", json=a_statement_body(member_id=JORDAN, description="  jordan  WAGES ")
    )
    assert this.status_code == 201, this.text
    previous = opened(sample, this.json()["id"])["previous_year"]
    assert previous["record_id"] == last.json()["id"]


def test_the_new_fields_are_saved_and_shown_as_entered(sample: TestClient) -> None:
    body = a_statement_body()
    body["statement"] = {
        **body["statement"],
        "employer": "Invented Pty",
        "leave_payment_type": "Annual leave",
        "paid_leave_amount": "50.00",
        "lump_sum_d": "700.50",
        "reportable_fringe_benefits": "90.00",
        "reportable_fringe_benefits_taxable": "40.00",
    }
    created = sample.post("/api/tax/records", json=body)
    assert created.status_code == 201, created.text
    shown = opened(sample, created.json()["id"])["statement"]
    assert shown["employer"] == "Invented Pty"
    assert shown["leave_payment_type"] == "Annual leave"
    assert shown["salary_sacrifice_type"] == ""
    assert shown["paid_leave_amount"] == "50.00"
    assert shown["lump_sum_d"] == "700.50"
    assert shown["reportable_fringe_benefits_taxable"] == "40.00"


def test_the_salary_sacrifice_amount_is_saved_negative_as_the_ato_prints_it(
    sample: TestClient,
) -> None:
    body = a_statement_body()
    body["statement"] = {**body["statement"], "salary_sacrifice_amount": "-3000.00"}
    created = sample.post("/api/tax/records", json=body)
    assert created.status_code == 201, created.text
    shown = opened(sample, created.json()["id"])["statement"]
    assert shown["salary_sacrifice_amount"] == "-3000.00"
    # every other figure still refuses a negative, with the message beside it
    body["statement"] = {**body["statement"], "bonuses_commissions": "-1.00"}
    res = sample.post("/api/tax/records", json=body)
    assert res.status_code == 422
    assert [e["field"] for e in res.json()["errors"]] == ["bonuses_commissions"]


def test_a_bad_new_field_is_a_validation_error_on_that_field(sample: TestClient) -> None:
    body = a_statement_body()
    body["statement"] = {**body["statement"], "lump_sum_a": "-1", "bonuses_commissions": "abc"}
    res = sample.post("/api/tax/records", json=body)
    assert res.status_code == 422
    fields = {e["field"] for e in res.json()["errors"]}
    assert {"lump_sum_a", "bonuses_commissions"} <= fields
    body["statement"] = {**a_statement_body()["statement"], "employer": "x" * 81}
    res = sample.post("/api/tax/records", json=body)
    assert res.status_code == 422
    assert [e["field"] for e in res.json()["errors"]] == ["employer"]


def test_a_lump_sum_in_the_register_is_in_its_label_total(sample: TestClient) -> None:
    body = a_statement_body()
    body["statement"] = {**body["statement"], "lump_sum_a": "300.00", "allowances": "40.00"}
    assert sample.post("/api/tax/records", json=body).status_code == 201
    res = sample.get("/api/tax/label-totals", params={"financial_year": 2026, "member_id": ALEX})
    by_label = {t["label_key"]: t["amount"] for t in res.json()["totals"]}
    assert by_label["employer_lump_sum"] == "300.00"
    assert by_label["allowances"] == "40.00"


def test_a_taxable_fringe_part_above_the_total_is_refused_with_a_message(
    sample: TestClient,
) -> None:
    body = a_statement_body()
    body["statement"] = {
        **body["statement"],
        "reportable_fringe_benefits": "10.00",
        "reportable_fringe_benefits_taxable": "20.00",
    }
    res = sample.post("/api/tax/records", json=body)
    assert res.status_code == 422
    (error,) = res.json()["errors"]
    assert "cannot be more than" in error["message"]
