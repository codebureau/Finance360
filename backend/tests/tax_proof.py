"""The logic behind the local proof against a lodged return (ADR-0019 s8).

The proof builds the engine inputs and the expected assessed outcome from files kept under
``private/tax_proof/`` (gitignored, never in the repo, absent in CI), runs the estimate and compares
to the cent. This module is the testable part: it parses a case file, builds the ``TaxInput``,
compares and reports. It reads nothing by itself; ``test_tax_proof_real.py`` finds the folder.

**What is reported, and what never is.** A finding names a member by position ("member 1"), an item
by its result key or Return label key, and gives a *difference* (engine less lodged) or says the
engine has no value to compare. It never echoes an entered figure, an expected figure or a name, so
the failure text is safe to paste into an issue or PR description.

The case file format is documented in ``docs/tax-proof.md``.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, cast

from finance360.capabilities.tax import engine, labels
from finance360.capabilities.tax.input import (
    TAX_INPUT_VERSION,
    MemberInput,
    Provenance,
    TaxInput,
    Total,
)
from finance360.capabilities.tax.rules import TaxRules
from finance360.core.money import Money, MoneyError, format_amount, parse_amount

FOLDER_ENV = "FINANCE360_PRIVATE_TAX_PROOF"

#: Lodged result keys the engine can match, and the ``MemberResult`` attribute each compares with.
RESULT_KEYS: dict[str, str] = {
    "taxable_income": "taxable_income",
    "tax_on_taxable_income": "tax_on_taxable_income",
    "offsets": "offsets",
    "medicare_levy": "medicare_levy",
    "medicare_levy_surcharge": "medicare_levy_surcharge",
    "help_repayment": "help_repayment",
    "total_tax": "total_tax",
    "credits": "credits",
    "net_outcome": "net_outcome",
}


class CaseError(ValueError):
    """A proof case file is malformed. The message names a position or key, never a value."""


@dataclass(frozen=True)
class MemberCase:
    born_on: date
    #: Label key to the entered total: an amount text, a number of days or a flag.
    labels: dict[str, str | int | bool]
    #: Result key to the lodged amount text.
    expected: dict[str, str]
    #: Lodged lines the engine has no result key for, by name (the amount is not needed).
    other: tuple[str, ...]


@dataclass(frozen=True)
class Case:
    financial_year: int
    dependants: int
    members: tuple[MemberCase, ...]


@dataclass(frozen=True)
class Finding:
    member: int  # 1-based position in the file
    item: str
    #: ``difference`` (the engine less the lodged amount), ``unmatched`` (no engine value to
    #: compare: an unknown result key or a lodged line the engine cannot match) or ``unmodelled``
    #: (a Return label with a figure that the engine does not model).
    kind: str
    difference: Money | None = None

    def line(self) -> str:
        who = f"member {self.member}"
        if self.kind == "difference" and self.difference is not None:
            return f"{who}: {self.item} differs by {format_amount(self.difference)}"
        if self.kind == "unmodelled":
            return f"{who}: {self.item} has a figure and is not modelled by the engine"
        return f"{who}: {self.item} has no engine value to compare"


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=lambda: [])
    matched: int = 0

    @property
    def clean(self) -> bool:
        return not self.findings

    def text(self) -> str:
        head = f"{self.matched} item(s) matched to the cent; {len(self.findings)} did not."
        return "\n".join([head, *(f.line() for f in self.findings)])


# --- finding the files -----------------------------------------------------------------------


def default_folder() -> Path | None:
    """``$FINANCE360_PRIVATE_TAX_PROOF``, or ``private/tax_proof`` in the main checkout (found
    through git's common dir, so it works from an agent worktree). Not read here."""
    if env := os.environ.get(FOLDER_ENV):
        return Path(env)
    here = Path(__file__).resolve().parent
    try:
        common = subprocess.run(  # noqa: S603
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
            cwd=here,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return Path(common).parent / "private" / "tax_proof"


def latest_case_file(folder: Path | None) -> Path | None:
    """The file of the most recent financial year: ``<FY>.json`` with the highest ``FY``."""
    if folder is None or not folder.is_dir():
        return None
    files = [p for p in folder.glob("*.json") if p.stem.isdigit()]
    return max(files, key=lambda p: int(p.stem), default=None)


# --- parsing ---------------------------------------------------------------------------------


def _obj(raw: object, where: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise CaseError(f"{where} must be an object")
    return cast("dict[str, Any]", raw)


def load_case(path: Path) -> Case:
    try:
        data = _obj(json.loads(path.read_text(encoding="utf-8")), "the file")
    except json.JSONDecodeError:
        raise CaseError("the file is not valid JSON") from None
    year, dependants = data.get("financial_year"), data.get("dependants", 0)
    if not isinstance(year, int) or isinstance(year, bool):
        raise CaseError("financial_year must be a whole number")
    if year != int(path.stem):
        raise CaseError("financial_year must match the file name")
    if not isinstance(dependants, int) or isinstance(dependants, bool):
        raise CaseError("dependants must be a whole number")
    raw_members = data.get("members")
    if not isinstance(raw_members, list) or not 1 <= len(raw_members) <= 2:  # pyright: ignore[reportUnknownArgumentType]
        raise CaseError("members must list one or two members")
    members: list[MemberCase] = []
    for position, raw in enumerate(cast("list[Any]", raw_members), start=1):
        where = f"member {position}"
        body = _obj(raw, where)
        try:
            born = date.fromisoformat(str(body.get("born_on")))
        except ValueError:
            raise CaseError(f"{where}: born_on must be an ISO date") from None
        entered = _obj(body.get("labels"), f"{where}: labels")
        expected = _obj(body.get("expected"), f"{where}: expected")
        other = body.get("other", [])
        if not isinstance(other, list) or not all(isinstance(n, str) for n in other):  # pyright: ignore[reportUnknownVariableType]
            raise CaseError(f"{where}: other must list names")
        member = MemberCase(
            born,
            {k: v for k, v in entered.items()},
            {k: str(v) for k, v in expected.items()},
            tuple(cast("list[str]", other)),
        )
        _check_net_outcome(member, where)
        members.append(member)
    return Case(year, dependants, tuple(members))


#: The tax office rounds small amounts on the final result, so the lodged net outcome can sit a
#: few cents from credits less total tax.
NET_OUTCOME_TOLERANCE = Money(5, "AUD")

_TAX_COMPONENTS = (
    "tax_on_taxable_income",
    "offsets",
    "medicare_levy",
    "medicare_levy_surcharge",
    "help_repayment",
)


def _check_net_outcome(member: MemberCase, where: str) -> None:
    """The lodged ``net_outcome`` must be credits less total tax, within a few cents. A refund is
    positive and an amount payable negative; a flipped sign is the usual mistake. The message names
    the member and never echoes a figure."""
    exp = member.expected
    if member.other or "credits" not in exp or "net_outcome" not in exp:
        return  # other assessed lines (an offset, say) legitimately move net_outcome
    if "total_tax" in exp:
        total = _amount(exp["total_tax"], f"{where}: expected total_tax")
    elif all(k in exp for k in _TAX_COMPONENTS):
        parts = {k: _amount(exp[k], f"{where}: expected {k}") for k in _TAX_COMPONENTS}
        total = (
            parts["tax_on_taxable_income"]
            - parts["offsets"]
            + parts["medicare_levy"]
            + parts["medicare_levy_surcharge"]
            + parts["help_repayment"]
        )
    else:
        return
    credits = _amount(exp["credits"], f"{where}: expected credits")
    net = _amount(exp["net_outcome"], f"{where}: expected net_outcome")
    gap = abs((credits - total).amount_minor - net.amount_minor)
    if gap > NET_OUTCOME_TOLERANCE.amount_minor:
        raise CaseError(
            f"{where}: net_outcome does not equal credits less total tax; check its sign "
            "(a refund is positive, an amount payable negative) and the other expected figures"
        )


# --- building and comparing --------------------------------------------------------------------


def _amount(text: object, where: str) -> Money:
    """An amount text as AUD Money. The error names the place and never echoes the text."""
    if not isinstance(text, str):
        raise CaseError(f"{where} must be an amount text")
    try:
        return parse_amount(text, "AUD")
    except MoneyError:
        raise CaseError(f"{where} is not an amount with at most 2 decimal places") from None


def build_input(case: Case, rules: TaxRules) -> TaxInput:
    members: list[MemberInput] = []
    for position, member in enumerate(case.members, start=1):
        totals: list[tuple[str, Total]] = []
        for key, raw in member.labels.items():
            label = labels.get(key)
            if label is None:
                raise CaseError(f"member {position}: {key} is not a Return label")
            if label.kind == labels.MONEY:
                totals.append((key, _amount(raw, f"member {position}: {key}")))
            else:
                totals.append((key, cast(Total, raw)))
        members.append(MemberInput(position, member.born_on, tuple(totals)))
    return TaxInput(
        input_version=TAX_INPUT_VERSION,
        financial_year=case.financial_year,
        members=tuple(members),
        dependants=case.dependants,
        rules=rules,
        provenance=(Provenance("source", "tax proof"),),
    )


def compare(case: Case, result: engine.TaxResult) -> Report:
    """Compare the estimate with the lodged outcome, to the cent, reporting every difference."""
    report = Report()
    for position, (member, got) in enumerate(zip(case.members, result.members, strict=True), 1):
        for key, text in member.expected.items():
            attribute = RESULT_KEYS.get(key)
            if attribute is None:
                report.findings.append(Finding(position, key, "unmatched"))
                continue
            lodged = _amount(text, f"member {position}: expected {key}")
            difference = getattr(got, attribute).amount_minor - lodged.amount_minor
            if difference == 0:
                report.matched += 1
            else:
                report.findings.append(
                    Finding(position, key, "difference", Money(difference, "AUD"))
                )
        for name in member.other:
            report.findings.append(Finding(position, name, "unmatched"))
    for item in result.unmodelled:
        # the member id is the 1-based position (``build_input``)
        report.findings.append(Finding(item.member_id, item.label_key, "unmodelled"))
    return report
