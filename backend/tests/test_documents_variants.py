"""Golden tests for the layouts a real ATO print can take (ADR-0024 section 8).

The ATO prints only the sections an employer reported, so a statement may lack the employer super
row, wrap its long label, repeat the leave rows or state only the total. The committed synthetic
PDFs (``scripts/make_ato_income_statement_fixtures.py``, invented values) read to exact figures.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from finance360.capabilities.documents import loader, registry, service
from finance360.capabilities.documents import reading as r
from finance360.capabilities.documents.profile import load_profile, to_json
from finance360.core.money import BASE_CURRENCY, Money
from finance360.plugins.documents.ato_income_statement import PROFILE
from support import register_income_statement_target

ROOT = Path(__file__).resolve().parents[2]
VARIANTS = ROOT / "fixtures" / "documents"
TARGET = "tax.income_statement"


@pytest.fixture(autouse=True)
def ato_profile() -> None:
    register_income_statement_target()
    registry.register(PROFILE)


def aud(text: str) -> Money:
    whole, _, cents = text.replace(",", "").partition(".")
    return Money(int(whole) * 100 + int(cents or 0), BASE_CURRENCY)


def read_variant(name: str) -> r.Reading:
    return service.read_pdf((VARIANTS / name).read_bytes(), TARGET, expected_fy=2026)


def figures(reading: r.Reading) -> dict[str, object]:
    return {f.key: f.value for f in reading.found}


def not_found(reading: r.Reading) -> dict[str, str]:
    return {n.key: n.reason for n in reading.not_found}


def test_two_leave_rows_are_added_up_and_each_source_row_is_kept() -> None:
    reading = read_variant("ato_income_statement_two_leave_rows.pdf")
    assert reading.outcome == r.WARNINGS  # optional rows are absent; nothing required is missing
    values = figures(reading)
    assert values["paid_leave_amount"] == aud("3750.00")  # 1,500.00 + 2,250.00
    assert values["leave_payment_type"] == "Annual leave; Long service leave"
    leave = next(f for f in reading.found if f.key == "paid_leave_amount")
    assert [(p.page, p.value) for p in leave.parts] == [(1, aud("1500.00")), (1, aud("2250.00"))]
    # The statement does not carry bonuses or a salary sacrifice: not filled, counted as zero.
    assert {"bonuses_commissions", "salary_sacrifice_amount"} <= set(not_found(reading))
    (total,) = [c for c in reading.checks if c.kind == "sum"]
    assert total.status == r.AGREE  # 70,000.00 + 3,750.00 = 73,750.00
    assert total.assumed_zero == ("bonuses_commissions", "salary_sacrifice_amount")


def test_an_allowance_row_with_a_sub_category_is_read_as_the_allowances_total() -> None:
    """The Allowances section's own Total is read (the row's sub-category is a label we cannot
    name, so it is not read as text: see the profile README)."""
    reading = read_variant("ato_income_statement_allowance.pdf")
    allowances = next(f for f in reading.found if f.key == "allowances")
    assert (allowances.value, allowances.page, allowances.how) == (aud("600.00"), 1, r.READ)
    # The Total under Deductions is a different section's: it keeps its own figure.
    assert figures(reading)["deductions_total"] == aud("350.00")
    assert [c.status for c in reading.checks if c.kind == "sum"] == [
        r.AGREE
    ]  # allowances are not in the gross sum


def test_a_statement_with_no_allowance_rows_counts_them_as_nil() -> None:
    allowances = next(
        f for f in read_variant("ato_income_statement_short.pdf").found if f.key == "allowances"
    )
    assert (allowances.value, allowances.how) == (aud("0.00"), r.ABSENT)


def test_each_paid_leave_under_its_own_sub_heading_is_added_up() -> None:
    reading = read_variant("ato_income_statement_leave_subheadings.pdf")
    values = figures(reading)
    assert values["paid_leave_amount"] == aud("3750.00")  # both, not the second only
    assert values["leave_payment_type"] == "Annual leave; Long service leave"
    leave = next(f for f in reading.found if f.key == "paid_leave_amount")
    assert [p.value for p in leave.parts] == [aud("1500.00"), aud("2250.00")]
    assert [c.status for c in reading.checks if c.kind == "sum"] == [r.AGREE]


def test_a_statement_without_the_super_row_is_still_claimed_and_read() -> None:
    reading = read_variant("ato_income_statement_no_super_section.pdf")
    assert reading.outcome == r.WARNINGS
    assert figures(reading)["gross_payments"] == aud("60000.00")
    assert figures(reading)["tax_withheld"] == aud("12000.00")
    assert not_found(reading)["employer_super"] == r.LABEL_NOT_FOUND
    assert [(c.kind, c.status) for c in reading.checks] == [("sum", r.AGREE), ("period", r.AGREE)]


def test_a_super_label_that_wraps_onto_a_second_line_still_reads_its_amount() -> None:
    reading = read_variant("ato_income_statement_wrapped_super_label.pdf")
    employer_super = next(f for f in reading.found if f.key == "employer_super")
    assert (employer_super.value, employer_super.page) == (aud("10140.00"), 2)
    assert employer_super.label == "Employer superannuation contribution"
    assert figures(reading)["gross_payments"] == aud("41000.00")
    assert [c.status for c in reading.checks if c.kind == "sum"] == [r.AGREE]  # 40,000 + 1,000


def test_the_short_layout_reads_what_is_there_and_says_why_the_sum_was_skipped() -> None:
    reading = read_variant("ato_income_statement_short.pdf")
    assert reading.outcome == r.WARNINGS
    values = figures(reading)
    assert values["gross_payments"] == aud("52000.00")
    assert values["tax_withheld"] == aud("9100.00")
    assert values["deductions_total"] == aud("120.00")
    # Only the total is stated, and "Gross amount" is not a part that may count as zero.
    assert not_found(reading)["gross_amount"] == r.LABEL_NOT_FOUND
    (total,) = [c for c in reading.checks if c.kind == "sum"]
    assert total.status == r.SKIPPED
    assert "gross_amount" in total.message


def test_two_statements_for_one_year_each_read_on_their_own() -> None:
    first = read_variant("ato_income_statement_payer_a.pdf")
    second = read_variant("ato_income_statement_payer_b.pdf")
    for reading in (first, second):
        assert reading.outcome == r.WARNINGS
        assert figures(reading)["period"] == 2026
        assert [c.status for c in reading.checks] == [r.AGREE, r.AGREE]
    assert figures(first)["gross_payments"] == aud("30000.00")
    assert figures(second)["gross_payments"] == aud("20000.00")
    assert figures(first)["tax_withheld"] == aud("4500.00")
    assert figures(second)["tax_withheld"] == aud("3000.00")


def test_a_saved_version_1_profile_still_loads_and_reads() -> None:
    """A Document profile row saved before v2 has no ``repeats`` or ``zero_in_sums`` and the old
    claim: the schema is additive, so it loads with both off and reads as it always did."""
    v1 = to_json(PROFILE)
    v1["version"] = 1
    v1["claim"] = [*PROFILE.claim[:2], "Employer superannuation contribution liability"]
    for field in v1["fields"]:
        field.pop("repeats", None)
        field.pop("zero_in_sums", None)
    profile = load_profile(v1)
    assert not any(f.repeats or f.zero_in_sums for f in profile.fields)
    sample = ROOT / "fixtures" / "sample_household" / "documents"
    document = loader.load_pdf((sample / "ato_income_statement_sample.pdf").read_bytes())
    reading = service.read_with_profile(document, profile, expected_fy=2026)
    assert (reading.outcome, reading.profile_version) == (r.OK, 1)
    # Under v1 the same statement with a wrapped super label is still not claimed (as it was).
    name = "ato_income_statement_wrapped_super_label.pdf"
    wrapped = loader.load_pdf((VARIANTS / name).read_bytes())
    assert service.read_with_profile(wrapped, profile).outcome == r.UNRECOGNISED
