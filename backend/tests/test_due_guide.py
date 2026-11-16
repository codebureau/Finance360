"""The Guide and the Due rules cannot drift (ADR-0020 section 5).

For every registered rule, ``04-what-to-provide.md`` must have a line that names the rule
(``**<guide_item>**``), states its cadence for each Effort level exactly as the rule's table
generates it, and carries the marker. Conversely a line carries the marker only if a rule has its
``guide_item``. A new rule with no Guide line, a changed cadence the Guide does not state, or a
promise with no rule fails here.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest

from finance360.core import due as core_due
from finance360.core.due import IMPORTANT, DueRule, Every
from finance360.main import register_due_rules

GUIDE = Path(__file__).resolve().parents[2] / "frontend" / "src" / "guide" / "04-what-to-provide.md"
LINES = GUIDE.read_text(encoding="utf-8").splitlines()


@pytest.fixture(autouse=True)
def registered() -> Iterator[None]:
    saved = dict(core_due._RULES)
    core_due._RULES.clear()
    register_due_rules()
    yield
    core_due._RULES.clear()
    core_due._RULES.update(saved)


def problems(rules: Sequence[DueRule], lines: Sequence[str]) -> list[str]:
    """Every way the Guide and the rules disagree, in both directions."""
    found: list[str] = []
    for rule in rules:
        mine = [line for line in lines if f"**{rule.guide_item}**" in line]
        if len(mine) != 1:
            found.append(f"{rule.key}: expected one Guide line for **{rule.guide_item}**")
            continue
        if core_due.cadence_string(rule) not in mine[0]:
            found.append(
                f"{rule.key}: the Guide line does not state '{core_due.cadence_string(rule)}'"
            )
        if core_due.GUIDE_MARKER not in mine[0]:
            found.append(f"{rule.key}: the Guide line lacks the marker")
    for line in lines:
        if core_due.GUIDE_MARKER in line and not any(
            f"**{rule.guide_item}**" in line for rule in rules
        ):
            found.append(f"A Guide line promises What's due but no rule has its item: {line[:80]}")
    return found


def a_rule(item: str, days: int) -> DueRule:
    return DueRule(
        key="test.rule",
        importance=IMPORTANT,
        cadence={"light": Every(days), "steady": Every(days), "thorough": Every(days)},
        suits=("whole",),
        guide_item=item,
        interested=lambda _s: True,
        check=lambda _s, _t, _l: (),
    )


def test_the_cadence_string_is_generated_from_the_table() -> None:
    manual = core_due.get("position.manual_valuation")
    slow = core_due.get("position.slow_valuation")
    statement = core_due.get("tax.income_statement")
    assert manual is not None and slow is not None and statement is not None
    assert (
        core_due.cadence_string(manual)
        == "Light: every 3 months · Steady: monthly · Thorough: monthly"
    )
    assert core_due.cadence_string(slow) == "Light: not asked · Steady: yearly · Thorough: yearly"
    assert core_due.cadence_string(statement) == (
        "Light: from 15 July · Steady: from 15 July · Thorough: from 15 July"
    )


def test_the_rules_are_registered() -> None:
    assert {r.key for r in core_due.rules()} == {
        "position.manual_valuation",
        "position.super_valuation",
        "position.slow_valuation",
        "tax.income_statement",
        "rule_figures.not_captured",
        "rule_figures.release_differs",
        "rule_figures.no_longer_fits",
        "rule_figures.now_firm",
    }


def test_the_guide_matches_every_registered_rule_both_ways() -> None:
    assert problems(core_due.rules(), LINES) == []


def test_the_check_notices_a_rule_with_no_guide_line() -> None:
    rules = [*core_due.rules(), a_rule("A new thing to refresh", 31)]
    assert problems(rules, LINES) == [
        "test.rule: expected one Guide line for **A new thing to refresh**"
    ]


def test_the_check_notices_a_cadence_the_guide_does_not_state() -> None:
    changed = Every(10)
    manual = core_due.get("position.manual_valuation")
    assert manual is not None
    drifted = DueRule(
        key=manual.key,
        importance=manual.importance,
        cadence={"light": changed, "steady": changed, "thorough": changed},
        suits=manual.suits,
        guide_item=manual.guide_item,
        interested=manual.interested,
        check=manual.check,
    )
    others = [r for r in core_due.rules() if r.key != manual.key]
    [problem] = problems([drifted, *others], LINES)
    assert "every 10 days" in problem


def test_the_check_notices_a_promise_with_no_rule() -> None:
    rules = [r for r in core_due.rules() if r.key != "tax.income_statement"]
    [problem] = problems(rules, LINES)
    assert "promises What's due but no rule" in problem
