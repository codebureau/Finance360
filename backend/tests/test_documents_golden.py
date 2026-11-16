"""Golden tests: the committed synthetic ATO income statement PDFs read to an exact Reading
(ADR-0024 section 8). The values are invented (``scripts/make_ato_income_statement_fixtures.py``);
the tests never regenerate the PDFs, so CI needs no browser."""

from __future__ import annotations

from pathlib import Path

import pytest

from finance360.capabilities.documents import loader, registry, service
from finance360.capabilities.documents import reading as r
from finance360.capabilities.documents.model import lines_of
from finance360.core.money import BASE_CURRENCY, Money
from finance360.plugins.documents.ato_income_statement import PROFILE
from support import register_income_statement_target

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "fixtures" / "sample_household" / "documents" / "ato_income_statement_sample.pdf"
VARIANTS = ROOT / "fixtures" / "documents"
TARGET = "tax.income_statement"


@pytest.fixture(autouse=True)
def ato_profile() -> None:
    register_income_statement_target()
    registry.register(PROFILE)


def aud(text: str) -> Money:
    whole, _, cents = text.replace(",", "").partition(".")
    return Money(int(whole) * 100 + int(cents or 0), BASE_CURRENCY)


#: Every figure the sample statement states, as the Reading must give it, page and label.
EXPECTED: list[tuple[str, object, int, str]] = [
    ("period", 2026, 1, "Period"),
    ("gross_amount", aud("80000.00"), 1, "Gross amount"),
    ("bonuses_commissions", aud("5000.00"), 1, "Bonuses and commissions"),
    ("leave_payment_type", "Annual leave", 1, "Leave payment type"),
    ("paid_leave_amount", aud("2500.00"), 1, "Paid leave amount"),
    ("salary_sacrifice_type", "Super", 1, "Salary sacrifice type"),
    ("salary_sacrifice_amount", aud("-3000.00"), 1, "Salary sacrifice amount"),
    ("gross_payments", aud("84500.00"), 1, "Total gross amount"),
    ("tax_withheld", aud("18250.00"), 1, "PAYGW amount"),
    ("lump_sum_a", aud("0.00"), 1, "Lump sum payment A"),
    ("lump_sum_b", aud("0.00"), 1, "Lump sum payment B"),
    ("lump_sum_d", aud("0.00"), 1, "Lump sum payment D"),
    ("lump_sum_e", aud("500.00"), 1, "Lump sum payment E"),
    ("lump_sum_w", aud("0.00"), 1, "Lump sum payment W"),
    ("allowances", aud("0.00"), 0, ""),  # an empty Allowances section: nil, counted as zero
    ("deductions_total", aud("350.00"), 1, "Total"),
    ("employer_super", aud("10140.00"), 2, "Employer superannuation contribution liability"),
    (
        "reportable_fringe_benefits_taxable",
        aud("1000.00"),
        2,
        "Reportable fringe benefits - Taxable employer",
    ),
    ("reportable_employer_super", aud("3000.00"), 2, "Reportable Employer Super Contribution"),
    ("reportable_fringe_benefits", aud("1880.00"), 2, "Reportable fringe benefits - total"),
]


def triples(reading: r.Reading) -> list[tuple[str, object, int, str]]:
    return [(f.key, f.value, f.page, f.label) for f in reading.found]


def test_the_sample_statement_reads_to_the_exact_golden_reading() -> None:
    reading = service.read_pdf(SAMPLE.read_bytes(), TARGET, expected_fy=2026)
    assert reading.outcome == r.OK
    assert (reading.profile_key, reading.profile_version) == ("ato-income-statement", 3)
    assert triples(reading) == EXPECTED
    assert reading.not_found == ()
    assert [(c.kind, c.status, c.fields) for c in reading.checks] == [
        (
            "sum",
            r.AGREE,
            (
                "gross_payments",
                "gross_amount",
                "bonuses_commissions",
                "paid_leave_amount",
                "salary_sacrifice_amount",
            ),
        ),
        ("period", r.AGREE, ("period",)),
    ]


def test_the_payer_and_identifier_block_is_never_read_into_a_figure() -> None:
    reading = service.read_pdf(SAMPLE.read_bytes(), TARGET)
    keys = {f.key for f in reading.found}
    assert keys == {key for key, *_ in EXPECTED}  # no payer, ABN, employee or BMS key
    assert not [f for f in reading.found if "SAMPLE" in str(f.value) or "Pty" in str(f.value)]


def test_the_same_statement_through_the_text_loader_reads_the_same_figures() -> None:
    document = loader.load_pdf(SAMPLE.read_bytes())
    text = "\n".join(line.text for page in document.pages for line in lines_of(page))
    from_text = service.read_text(text, TARGET, expected_fy=2026)
    assert from_text.outcome == r.OK
    assert [(k, v, label) for k, v, _, label in triples(from_text)] == [
        (k, v, label) for k, v, _, label in EXPECTED
    ]


def test_a_missing_required_label_needs_repair_and_still_fills_what_was_found() -> None:
    reading = service.read_pdf(
        (VARIANTS / "ato_income_statement_no_paygw.pdf").read_bytes(), TARGET
    )
    assert reading.outcome == r.NEEDS_REPAIR
    assert triples(reading) == [t for t in EXPECTED if t[0] != "tax_withheld"]
    assert [(n.key, n.reason, n.required) for n in reading.not_found] == [
        ("tax_withheld", r.LABEL_NOT_FOUND, True)
    ]


def test_parts_that_do_not_add_up_warn_with_every_figure_still_filled() -> None:
    reading = service.read_pdf(
        (VARIANTS / "ato_income_statement_sums_differ.pdf").read_bytes(), TARGET
    )
    assert reading.outcome == r.WARNINGS
    assert [(c.kind, c.status) for c in reading.checks] == [("sum", r.DISAGREE)]
    assert len(reading.found) == len(EXPECTED)  # nothing withheld: the owner sees the figures
    gross = {f.key: f.value for f in reading.found}["gross_payments"]
    assert gross == aud("84501.00")  # exactly what the page states


def test_a_statement_for_another_year_warns_with_the_period_check() -> None:
    reading = service.read_pdf(SAMPLE.read_bytes(), TARGET, expected_fy=2025)
    assert reading.outcome == r.WARNINGS
    assert [(c.kind, c.status) for c in reading.checks if c.kind == "period"] == [
        ("period", r.DISAGREE)
    ]


def test_a_different_document_is_unrecognised() -> None:
    reading = service.read_pdf((VARIANTS / "sample_bank_statement.pdf").read_bytes(), TARGET)
    assert reading.outcome == r.UNRECOGNISED
    assert (reading.profile_key, reading.found, reading.checks) == (None, (), ())


def test_a_target_with_no_profile_is_refused() -> None:
    with pytest.raises(service.UnknownTarget):
        service.read_pdf(SAMPLE.read_bytes(), "no.such.target")
