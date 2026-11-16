"""Generate the synthetic ATO income statement PDFs (ADR-0024 section 8).

Every name, number and amount here is INVENTED. The layout (headings, labels and their order)
follows the ATO income statement page; the values are made up. Chromium "prints" an HTML page to
PDF, as a browser does for the real page, so the result is the same kind of file: a PDF with a text
layer.

The tests never run this: they read the committed PDFs, so CI needs no browser. Run it only to
change a fixture, then commit the PDFs and update the golden tests:

    uv run python scripts/make_ato_income_statement_fixtures.py

One-time: ``uv run playwright install chromium``. Writes:

* ``fixtures/sample_household/documents/ato_income_statement_sample.pdf`` (the household's file)
* ``fixtures/documents/ato_income_statement_no_paygw.pdf`` (a required label is missing)
* ``fixtures/documents/ato_income_statement_sums_differ.pdf`` (the total is 1.00 out)
* ``fixtures/documents/sample_bank_statement.pdf`` (a different document: nothing claims it)
* the layouts a real statement can take, since the ATO prints only the sections an employer
  reported: ``ato_income_statement_no_super_section.pdf`` (no employer super row),
  ``..._wrapped_super_label.pdf`` (the super label wraps onto a second line),
  ``..._two_leave_rows.pdf`` (leave paid twice), ``..._short.pdf`` (only the total gross amount)
  and ``..._payer_a.pdf`` / ``..._payer_b.pdf`` (two payers, one Member, one year); and
  ``..._allowance.pdf`` (an allowance row with a sub-category and a total) and
  ``..._leave_subheadings.pdf`` (each paid leave under its own sub-heading)

Existing files are left alone (a PDF carries a creation time, so regenerating changes its bytes);
pass ``--force`` to rewrite them all.
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
HOUSEHOLD = ROOT / "fixtures" / "sample_household" / "documents"
VARIANTS = ROOT / "fixtures" / "documents"

MINUS = "‑"  # the ATO prints a negative amount with a non-breaking hyphen

CSS = """
body { font-family: Arial, sans-serif; font-size: 11pt; margin: 0; }
h1 { font-size: 16pt; margin: 0 0 4pt 0; }
h2 { font-size: 13pt; margin: 14pt 0 4pt 0; border-bottom: 1px solid #888; }
h3 { font-size: 11pt; margin: 8pt 0 2pt 0; }
table { border-collapse: collapse; width: 100%; }
td { padding: 3pt 4pt; vertical-align: top; }
td.label { width: 55%; }
td.value { text-align: right; }
.page-break { break-after: page; }
"""


def row(label: str, value: str) -> str:
    cls = "label"
    return f'<tr><td class="{cls}">{label}</td><td class="value">{value}</td></tr>'


#: The salary and wages rows of the sample statement, in the order the ATO prints them.
FULL_SALARY = [
    ("Gross amount", "$80,000.00"),
    ("Bonuses and commissions", "$5,000.00"),
    ("Leave payment type", "Annual leave"),
    ("Paid leave amount", "$2,500.00"),
    ("Salary sacrifice type", "Super"),
    ("Salary sacrifice amount", MINUS + "$3,000.00"),
]
SUPER_ROW = ("Employer superannuation contribution liability", "$10,140.00")
#: The same row when the page wraps its long label (the value stays beside the first line).
WRAPPED_SUPER_ROW = ("Employer superannuation contribution<br>liability", "$10,140.00")


def statement_html(
    total_gross: str = "$84,500.00",
    paygw: str | None = "$18,250.00",
    *,
    employer: str = "Sample Employer Pty Ltd",
    salary: list[tuple[str, str]] | None = None,
    super_row: tuple[str, str] | None = SUPER_ROW,
    deductions: str = "$350.00",
    allowances_html: str = "",
) -> str:
    paygw_row = row("PAYGW amount", paygw) if paygw is not None else ""
    chosen = FULL_SALARY if salary is None else salary
    salary_rows = "".join(row(a, b) for a, b in chosen)
    super_html = row(*super_row) if super_row else ""
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{CSS}</style></head><body>
<h1>{employer}</h1>
<table>
{row("ABN", "00 000 000 000")}
{row("Income statement", "Sample year")}
{row("Status", "Tax ready")}
{row("Employee number", "SAMPLE-0001")}
{row("Branch", "000")}
{row("Employer ABN/Branch", "00 000 000 000 / 000")}
{row("BMS ID", "SAMPLE-BMS")}
{row("Period", "01/07/2025 - 30/06/2026")}
{row("Reported Date", "15/07/2026")}
</table>
<h2>Income</h2>
<h3>Salary and wages</h3>
<table>
{salary_rows}
{row("Total gross amount", total_gross)}
</table>
<h2>Tax withheld or foreign tax paid</h2>
<h3>Salary and wages</h3>
<table>{paygw_row}</table>
<h2>Lump sum amounts</h2>
<table>
{row("Lump sum payment A", "$0.00")}
{row("Lump sum payment B", "$0.00")}
{row("Lump sum payment D", "$0.00")}
{row("Lump sum payment E", "$500.00")}
{row("Lump sum payment W", "$0.00")}
</table>
<h2>Allowances</h2>
{allowances_html}
<h2>Deductions</h2>
<table>{row("Total", deductions)}</table>
<h2>Employer reported super</h2>
<div class="page-break"></div>
<table>{super_html}</table>
<h2>Other amounts</h2>
<table>
{row("Reportable fringe benefits - Taxable employer<br>status amount", "$1,000.00")}
{row("Community Development Employment Projects<br>payments", "$0.00")}
{row("Reportable Employer Super Contribution", "$3,000.00")}
{row("Reportable fringe benefits - total", "$1,880.00")}
</table>
</body></html>"""


BANK_HTML = f"""<!doctype html><html><head><meta charset="utf-8"><style>{CSS}</style></head><body>
<h1>Sample Bank Statement</h1>
<table>
{row("Account name", "Sample Savings")}
{row("Opening balance", "$1,000.00")}
{row("Closing balance", "$1,250.00")}
</table>
</body></html>"""


#: Statements for the layouts a real print can take. Invented names and amounts only.
LAYOUTS = {
    "ato_income_statement_no_super_section.pdf": statement_html(
        total_gross="$60,000.00",
        paygw="$12,000.00",
        salary=[("Gross amount", "$60,000.00")],
        super_row=None,
    ),
    "ato_income_statement_wrapped_super_label.pdf": statement_html(
        total_gross="$41,000.00",
        paygw="$8,200.00",
        salary=[("Gross amount", "$40,000.00"), ("Bonuses and commissions", "$1,000.00")],
        super_row=WRAPPED_SUPER_ROW,
    ),
    "ato_income_statement_two_leave_rows.pdf": statement_html(
        total_gross="$73,750.00",
        paygw="$15,000.00",
        salary=[
            ("Gross amount", "$70,000.00"),
            ("Leave payment type", "Annual leave"),
            ("Paid leave amount", "$1,500.00"),
            ("Leave payment type", "Long service leave"),
            ("Paid leave amount", "$2,250.00"),
        ],
        super_row=WRAPPED_SUPER_ROW,
    ),
    "ato_income_statement_allowance.pdf": statement_html(
        total_gross="$60,000.00",
        paygw="$12,000.00",
        salary=[("Gross amount", "$60,000.00")],
        super_row=WRAPPED_SUPER_ROW,
        allowances_html=(
            "<h3>Salary and wages</h3><table>"
            + row("Overtime meal allowance", "$600.00")
            + row("Total", "$600.00")
            + "</table>"
        ),
    ),
    "ato_income_statement_leave_subheadings.pdf": statement_html(
        total_gross="$73,750.00",
        paygw="$15,000.00",
        salary=[
            ("Gross amount", "$70,000.00"),
            ("<b>Paid leave</b>", ""),
            ("Leave payment type", "Annual leave"),
            ("Paid leave amount", "$1,500.00"),
            ("<b>Paid leave</b>", ""),
            ("Leave payment type", "Long service leave"),
            ("Paid leave amount", "$2,250.00"),
        ],
        super_row=WRAPPED_SUPER_ROW,
    ),
    "ato_income_statement_short.pdf": statement_html(
        total_gross="$52,000.00",
        paygw="$9,100.00",
        salary=[],
        super_row=WRAPPED_SUPER_ROW,
        deductions="$120.00",
    ),
    "ato_income_statement_payer_a.pdf": statement_html(
        total_gross="$30,000.00",
        paygw="$4,500.00",
        employer="Alpha Sample Pty Ltd",
        salary=[("Gross amount", "$30,000.00")],
        super_row=WRAPPED_SUPER_ROW,
    ),
    "ato_income_statement_payer_b.pdf": statement_html(
        total_gross="$20,000.00",
        paygw="$3,000.00",
        employer="Beta Sample Pty Ltd",
        salary=[("Gross amount", "$20,000.00")],
        super_row=WRAPPED_SUPER_ROW,
    ),
}


def main() -> None:
    force = "--force" in sys.argv
    HOUSEHOLD.mkdir(parents=True, exist_ok=True)
    VARIANTS.mkdir(parents=True, exist_ok=True)
    pages = {
        HOUSEHOLD / "ato_income_statement_sample.pdf": statement_html(),
        VARIANTS / "ato_income_statement_no_paygw.pdf": statement_html(paygw=None),
        VARIANTS / "ato_income_statement_sums_differ.pdf": statement_html(total_gross="$84,501.00"),
        VARIANTS / "sample_bank_statement.pdf": BANK_HTML,
        **{VARIANTS / name: html for name, html in LAYOUTS.items()},
    }
    pages = {path: html for path, html in pages.items() if force or not path.exists()}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        for path, html in pages.items():
            page.set_content(html)
            page.pdf(path=str(path), format="A4", margin={"top": "20mm", "bottom": "20mm"})
            print(f"wrote {path.relative_to(ROOT)}")
        browser.close()


if __name__ == "__main__":
    main()
