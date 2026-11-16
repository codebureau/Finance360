"""The proof's own logic, tested on synthetic case files in a temporary folder (never ``private/``).

``test_tax_proof_real.py`` is the local-only proof; this proves that its locating, parsing, matching
and reporting behave, and that its report never echoes a figure. All figures are invented
(``tax_support``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from tax_proof import (
    FOLDER_ENV,
    CaseError,
    build_input,
    compare,
    default_folder,
    latest_case_file,
    load_case,
)
from tax_support import FY, invented_rules

from finance360.capabilities.tax.engine import estimate

#: A case whose expected figures are the hand-worked goldens of ``test_tax_engine`` for the invented
#: rules: member 1 (80,000 salary) and member 2 (28,000 salary), a couple.
GOOD: dict[str, Any] = {
    "financial_year": FY,
    "dependants": 0,
    "members": [
        {
            "born_on": "1980-01-01",
            "labels": {"salary": "80000.00", "tax_withheld": "14000.00"},
            "expected": {
                "taxable_income": "80000.00",
                "tax_on_taxable_income": "10610.00",
                "offsets": "0.00",
                "medicare_levy": "2400.00",
                "medicare_levy_surcharge": "0.00",
                "help_repayment": "0.00",
                "credits": "14000.00",
                "net_outcome": "990.00",
            },
        },
        {
            "born_on": "1982-02-02",
            "labels": {"salary": "28000.00", "tax_withheld": "1000.00"},
            "expected": {"taxable_income": "28000.00", "medicare_levy": "720.00"},
        },
    ],
}


def write(folder: Path, case: dict[str, Any], year: int = FY) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{year}.json"
    path.write_text(json.dumps(case), encoding="utf-8")
    return path


def run(case: dict[str, Any], tmp_path: Path):
    parsed = load_case(write(tmp_path / "proof", case))
    return compare(parsed, estimate(build_input(parsed, invented_rules())))


def test_a_matching_case_reports_no_findings(tmp_path: Path) -> None:
    report = run(GOOD, tmp_path)
    assert report.clean and report.matched == 10
    assert report.text().startswith("10 item(s) matched to the cent; 0 did not.")


def test_a_difference_of_one_cent_is_reported_by_item_name_with_its_difference(
    tmp_path: Path,
) -> None:
    case = json.loads(json.dumps(GOOD))
    case["members"][0]["expected"]["medicare_levy"] = "2400.01"
    case["members"][1]["expected"]["medicare_levy"] = "719.50"
    report = run(case, tmp_path)
    assert [f.line() for f in report.findings] == [
        "member 1: medicare_levy differs by -0.01",
        "member 2: medicare_levy differs by 0.50",
    ]
    assert report.matched == 8 and not report.clean


def test_a_lodged_line_with_no_engine_key_is_reported_unmatched_by_name(tmp_path: Path) -> None:
    case = json.loads(json.dumps(GOOD))
    case["members"][0]["expected"]["private_health_rebate"] = "100.00"
    case["members"][1]["other"] = ["Zone offset"]
    report = run(case, tmp_path)
    assert [f.line() for f in report.findings] == [
        "member 1: private_health_rebate has no engine value to compare",
        "member 2: Zone offset has no engine value to compare",
    ]


def test_a_label_with_a_figure_the_engine_does_not_model_is_reported(tmp_path: Path) -> None:
    case = json.loads(json.dumps(GOOD))
    case["members"][1]["labels"]["government_allowances"] = "300.00"
    report = run(case, tmp_path)
    assert [f.line() for f in report.findings] == [
        "member 2: government_allowances has a figure and is not modelled by the engine"
    ]


def test_the_report_never_echoes_an_entered_or_lodged_figure_or_a_name(tmp_path: Path) -> None:
    case = json.loads(json.dumps(GOOD))
    case["members"][0]["expected"]["net_outcome"] = "123456.78"
    case["members"][0]["labels"]["government_allowances"] = "987654.32"
    case["members"][0]["other"] = ["Some lodged line"]
    text = run(case, tmp_path).text()
    for value in ("123456.78", "987654.32", "80000", "14000", "1980", "Alex"):
        assert value not in text


def test_the_latest_year_is_the_one_used(tmp_path: Path) -> None:
    folder = tmp_path / "proof"
    older = write(folder, {**GOOD, "financial_year": FY - 1}, FY - 1)
    newer = write(folder, GOOD)
    (folder / "notes.json").write_text("{}")
    (folder / "readme.txt").write_text("not a case")
    assert latest_case_file(folder) == newer != older


def test_it_skips_where_there_is_nothing_to_read(tmp_path: Path) -> None:
    assert latest_case_file(None) is None
    assert latest_case_file(tmp_path / "absent") is None
    empty = tmp_path / "empty"
    empty.mkdir()
    assert latest_case_file(empty) is None


def test_the_folder_can_be_pointed_elsewhere(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(FOLDER_ENV, str(tmp_path / "elsewhere"))
    assert default_folder() == tmp_path / "elsewhere"
    assert latest_case_file(default_folder()) is None


@pytest.mark.parametrize(
    "change",
    [
        lambda c: c.update(financial_year="2097"),
        lambda c: c.update(financial_year=FY + 5),  # does not match the file name
        lambda c: c.update(members=[]),
        lambda c: c.update(members=[GOOD["members"][0]] * 3),
        lambda c: c["members"][0].update(born_on="not a date"),
        lambda c: c["members"][0].update(labels=[]),
        lambda c: c["members"][0].update(other="x"),
    ],
)
def test_a_malformed_case_file_is_refused_with_a_message_that_holds_no_value(
    tmp_path: Path, change: Any
) -> None:
    case = json.loads(json.dumps(GOOD))
    change(case)
    with pytest.raises(CaseError) as error:
        load_case(write(tmp_path / "proof", case))
    assert "80000" not in str(error.value)


def test_a_bad_amount_is_refused_without_echoing_it(tmp_path: Path) -> None:
    case = json.loads(json.dumps(GOOD))
    case["members"][0]["labels"]["salary"] = "12345.678"
    parsed = load_case(write(tmp_path / "proof", case))
    with pytest.raises(CaseError) as error:
        build_input(parsed, invented_rules())
    assert "12345" not in str(error.value) and "salary" in str(error.value)
    case["members"][0]["labels"] = {"nonsense": "1.00"}
    with pytest.raises(CaseError):
        build_input(load_case(write(tmp_path / "proof", case)), invented_rules())


def test_a_file_that_is_not_json_is_refused(tmp_path: Path) -> None:
    folder = tmp_path / "proof"
    folder.mkdir()
    (folder / f"{FY}.json").write_text("{ not json")
    with pytest.raises(CaseError):
        load_case(folder / f"{FY}.json")
