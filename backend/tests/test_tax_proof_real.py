"""Local-only proof (ADR-0019 s8): the engine reproduces the latest lodged return to the cent.

The inputs and the assessed outcome come from ``private/tax_proof/<FY>.json`` (gitignored; absent in
CI and in fresh clones, so this skips there). The format is in ``docs/tax-proof.md``. The folder is
found in the main checkout via git's common dir, or via ``FINANCE360_PRIVATE_TAX_PROOF``.

The year's rules come from the registered AU Rule pack, so the guided capture
(``scripts/rulepack_update.py --fy <FY>``) must have run for that year first; if it has not, the
test fails naming the year and how to fix it, rather than passing over it.

On a mismatch the failure lists each item by result key or Return label key with its *difference*,
and each lodged line or label the engine cannot match. It never prints an entered or lodged figure
or a name, so the text is safe to paste into a PR description (``tax_proof.Finding``).
"""

from __future__ import annotations

import pytest
from tax_proof import CaseError, build_input, compare, default_folder, latest_case_file, load_case

from finance360.capabilities.tax import pack_rules
from finance360.capabilities.tax.engine import estimate
from finance360.core import rulepack

CASE_FILE = latest_case_file(default_folder())


@pytest.mark.skipif(CASE_FILE is None, reason="no lodged return in private/tax_proof")
def test_the_engine_reproduces_the_most_recently_lodged_return() -> None:
    assert CASE_FILE is not None
    try:
        case = load_case(CASE_FILE)
    except CaseError as error:
        pytest.fail(f"The proof case file is malformed: {error}")
    pack = rulepack.get("au")
    assert pack is not None
    try:
        rules = pack_rules.build_rules(pack, case.financial_year)
    except pack_rules.TaxRulesError as error:
        pytest.fail(str(error))
    try:
        tax_input = build_input(case, rules)
    except CaseError as error:
        pytest.fail(f"The proof case file is malformed: {error}")
    report = compare(case, estimate(tax_input))
    assert report.clean, report.text()
