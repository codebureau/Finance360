"""Guided Rule figure capture: parsing, unit and range validation, the year-on-year
flag, provisional notes, the rendered data module and the script run against a throwaway pack copy.

Every figure here is invented. The shipped pack is never written: the script tests copy it first.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from types import ModuleType

import pytest
from hypothesis import given
from hypothesis import strategies as st

from finance360.core.money import Money
from finance360.core.rate import Rate
from finance360.core.rulepack import (
    BRACKETS,
    CPI,
    FROZEN,
    INT,
    MONEY,
    RATE,
    Bracket,
    FigureSpec,
    RulePack,
    RulePackError,
)
from finance360.rulepacks import capture
from finance360.rulepacks.au import tax_specs

TODAY = date(2026, 11, 2)

# Invented specs with small, obviously fake ranges.
OFFSET = FigureSpec(
    "tax.demo.offset", MONEY, FROZEN, "Demo offset", "Invented source", "https://example.invalid/a",
    "The invented box.", 0, 100_000, "year",
)  # fmt: skip
SHARE = FigureSpec(
    "tax.demo.share", RATE, CPI, "Demo share", "Invented source", "https://example.invalid/b",
    "The invented rate.", 0, 400_000,
)  # fmt: skip
COUNT = FigureSpec(
    "tax.demo.count", INT, CPI, "Demo count", "Invented source", "https://example.invalid/c",
    "The invented count.", 1, 12,
)  # fmt: skip
TABLE = FigureSpec(
    "tax.demo.table", BRACKETS, CPI, "Demo table", "Invented source", "https://example.invalid/d",
    "The invented table.", 0, 50_000_00, "year", rate_high=500_000,
)  # fmt: skip
SPECS = (OFFSET, SHARE, COUNT, TABLE)


class Script:
    """Scripted answers; records what was asked and said."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []
        self.said: list[str] = []

    def ask(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)

    def say(self, text: str) -> None:
        self.said.append(text)

    @property
    def output(self) -> str:
        return "\n".join(self.said)


# --- parsing, unit and range ---------------------------------------------------------------


def test_money_parses_with_or_without_symbols_and_the_right_unit() -> None:
    assert capture.parse_figure(OFFSET, "123.45") == Money(12_345, "AUD")
    assert capture.parse_figure(OFFSET, "$1,000") == Money(100_000, "AUD")
    assert capture.parse_figure(OFFSET, "250 pa") == Money(25_000, "AUD")
    assert capture.parse_figure(OFFSET, "250 per year") == Money(25_000, "AUD")


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("1001", "outside the plausible range"),
        ("-5", "outside the plausible range"),
        ("12.5%", "percentage"),
        ("250 per fortnight", "per-fortnight"),
        ("250 pw", "per-week"),
        ("250 monthly", "per-month"),
        ("twelve", "dollar amount"),
        ("12.345", "dollar amount"),
    ],
)
def test_money_rejects_out_of_range_and_off_unit_entries(text: str, message: str) -> None:
    with pytest.raises(capture.FigureError, match=message):
        capture.parse_figure(OFFSET, text)


def test_a_fortnightly_key_accepts_fortnightly_and_rejects_yearly() -> None:
    fortnightly = FigureSpec(
        "tax.demo.fn", MONEY, FROZEN, "Fn", "s", "https://example.invalid/e", "w", 0, 1_000_00,
        "fortnight",
    )  # fmt: skip
    assert capture.parse_figure(fortnightly, "12 pf") == Money(1_200, "AUD")
    with pytest.raises(capture.FigureError, match="per-year"):
        capture.parse_figure(fortnightly, "12 pa")


def test_rate_parses_percent_and_rejects_dollars_and_out_of_range() -> None:
    assert capture.parse_figure(SHARE, "16.5%") == Rate(165_000)
    assert capture.parse_figure(SHARE, "16.5") == Rate(165_000)
    with pytest.raises(capture.FigureError, match="outside"):
        capture.parse_figure(SHARE, "41")
    with pytest.raises(capture.FigureError, match="dollar"):
        capture.parse_figure(SHARE, "$5")
    with pytest.raises(capture.FigureError, match="percentage"):
        capture.parse_figure(SHARE, "five")


def test_int_parses_whole_numbers_in_range() -> None:
    assert capture.parse_figure(COUNT, "7") == 7
    for bad in ("0", "13", "2.5", "x"):
        with pytest.raises(capture.FigureError):
            capture.parse_figure(COUNT, bad)


def test_brackets_parse_and_reject_bad_tables() -> None:
    table = capture.parse_figure(TABLE, "0:0, 1000:10, 3000.50:25")
    assert table == (
        Bracket(Money(0, "AUD"), Rate(0)),
        Bracket(Money(100_000, "AUD"), Rate(100_000)),
        Bracket(Money(300_050, "AUD"), Rate(250_000)),
    )
    for bad, message in [
        ("", "over:rate"),
        ("1000", "over:rate"),
        ("100:10", "first band starts at 0"),
        ("0:0,500:10,500:20", "ascend"),
        ("0:0,500:10,400:20", "ascend"),
        ("0:0,500:60", "rate 60%"),
        ("0:0,60000:10", "threshold"),
        ("0:0,500 pw:10", "per-week"),
    ]:
        with pytest.raises(capture.FigureError, match=message):
            capture.parse_figure(TABLE, bad)


@given(st.integers(0, 100_000))
def test_money_round_trips_through_its_canonical_text(minor: int) -> None:
    value = Money(minor, "AUD")
    assert capture.parse_figure(OFFSET, capture.format_figure(OFFSET, value)) == value


@given(st.integers(0, 400_000))
def test_rate_round_trips_through_its_canonical_text(ppm: int) -> None:
    assert capture.parse_figure(SHARE, capture.format_figure(SHARE, Rate(ppm))) == Rate(ppm)


@given(
    st.lists(st.integers(1, 5_000_000), max_size=5, unique=True).map(sorted),
    st.lists(st.integers(0, 500_000), min_size=6, max_size=6),
)
def test_a_table_round_trips_through_its_canonical_text(overs: list[int], ppms: list[int]) -> None:
    table = tuple(
        Bracket(Money(over, "AUD"), Rate(ppm)) for over, ppm in zip([0, *overs], ppms, strict=False)
    )
    assert capture.parse_figure(TABLE, capture.format_figure(TABLE, table)) == table


# --- year-on-year change -------------------------------------------------------------------


def test_a_change_beyond_the_limit_is_flagged_and_a_small_one_is_not() -> None:
    old = Money(10_000, "AUD")
    assert capture.large_change(OFFSET, old, Money(12_000, "AUD")) is None  # exactly 20%
    assert capture.large_change(OFFSET, old, Money(12_001, "AUD")) is not None
    assert capture.large_change(OFFSET, old, Money(7_999, "AUD")) is not None
    assert capture.large_change(OFFSET, old, old) is None
    assert capture.large_change(OFFSET, Money(0, "AUD"), Money(1, "AUD")) is not None
    assert capture.large_change(SHARE, Rate(100_000), Rate(125_000)) is not None
    assert capture.large_change(COUNT, 10, 11) is None


def test_a_table_change_is_flagged_by_band_count_or_any_band_moving() -> None:
    same = capture.parse_figure(TABLE, "0:0,1000:10")
    assert capture.large_change(TABLE, same, capture.parse_figure(TABLE, "0:0,1000:10")) is None
    assert "number of bands" in str(
        capture.large_change(TABLE, same, capture.parse_figure(TABLE, "0:0,1000:10,2000:20"))
    )
    assert capture.large_change(TABLE, same, capture.parse_figure(TABLE, "0:0,1000:20")) is not None
    assert capture.large_change(TABLE, same, capture.parse_figure(TABLE, "0:0,1010:10")) is None


# --- the guided session --------------------------------------------------------------------


def session(
    script: Script, data: capture.FigureData | None = None, fy: int = 2027
) -> tuple[capture.FigureData, list[str]]:
    data = data or capture.FigureData()
    changed = capture.capture_year(SPECS, fy, data, script.ask, script.say, TODAY)
    return data, changed


def test_the_session_shows_the_page_where_to_look_the_unit_and_the_range() -> None:
    script = Script("", "", "", "")
    session(script)
    assert "https://example.invalid/a" in script.output
    assert "address not yet confirmed" in script.output
    assert "The invented box." in script.output
    assert "dollars per year" in script.output and "plausible $0.00 to $1000.00" in script.output


def test_a_rejected_entry_is_asked_again_and_a_confirmed_one_is_stored() -> None:
    script = Script("5000", "12.345", "123.45", "y", "", "", "")
    data, changed = session(script)
    assert script.output.count("Rejected:") == 2
    assert data.figures[2027]["tax.demo.offset"] == Money(12_345, "AUD")
    assert data.retrieved[2027]["tax.demo.offset"] == "2026-11-02"
    assert "tax.demo.offset" not in data.provisional.get(2027, {})
    assert changed == ["tax.demo.offset"]


def test_a_figure_that_cannot_be_confirmed_is_stored_provisional_with_a_note() -> None:
    script = Script("123.45", "n", "announced, not yet law", "", "", "")
    data, _ = session(script)
    assert data.figures[2027]["tax.demo.offset"] == Money(12_345, "AUD")
    assert data.provisional[2027] == {"tax.demo.offset": "announced, not yet law"}


def test_a_provisional_figure_needs_a_note() -> None:
    script = Script("123.45", "n", "   ", "a real note", "", "", "")
    data, _ = session(script)
    assert "note is required" in script.output
    assert data.provisional[2027]["tax.demo.offset"] == "a real note"


def test_confirming_a_provisional_figure_clears_its_marker() -> None:
    data = capture.FigureData(
        figures={2027: {"tax.demo.offset": Money(12_345, "AUD")}},
        provisional={2027: {"tax.demo.offset": "announced"}},
    )
    _, changed = session(Script("123.45", "y", "", "", ""), data)
    assert data.provisional[2027] == {}
    assert changed == ["tax.demo.offset"]


def test_a_large_year_on_year_jump_prompts_and_declining_asks_again() -> None:
    data = capture.FigureData(figures={2026: {"tax.demo.offset": Money(10_000, "AUD")}})
    script = Script("500", "n", "101", "y", "", "", "")
    session(script, data)
    assert "Last year (2025-26): 100.00" in script.output
    assert "Large change." in script.output
    assert "  Keep this figure? [y/N]: " in script.prompts
    assert script.output.count("Large change.") == 1
    # 500 was declined; 101 (a 1% move) did not prompt; so the stored figure is 101.00.
    assert data.figures[2027]["tax.demo.offset"] == Money(10_100, "AUD")


def test_accepting_a_large_jump_stores_it() -> None:
    data = capture.FigureData(figures={2026: {"tax.demo.offset": Money(10_000, "AUD")}})
    session(Script("500", "y", "y", "", "", ""), data)
    assert data.figures[2027]["tax.demo.offset"] == Money(50_000, "AUD")


def test_blank_skips_q_stops_and_an_unchanged_figure_is_not_a_change() -> None:
    data = capture.FigureData(figures={2027: {"tax.demo.offset": Money(12_345, "AUD")}})
    _, changed = session(Script("123.45", "y", "q"), data)
    assert changed == []
    assert list(data.figures[2027]) == ["tax.demo.offset"]


# --- loading, the pack and the data module -------------------------------------------------


def test_load_rejects_an_undeclared_key_and_an_out_of_range_figure() -> None:
    with pytest.raises(RulePackError, match="undeclared"):
        capture.load_figures(SPECS, {2027: {"tax.demo.other": "1"}})
    with pytest.raises(RulePackError, match="outside"):
        capture.load_figures(SPECS, {2027: {"tax.demo.offset": "99999"}})


def test_a_rendered_module_reloads_to_the_same_data_and_the_hash_follows_it() -> None:
    data = capture.FigureData()
    session(
        Script("123.45", "n", 'announced "quoted" note', "16.5", "y", "7", "y", "0:0,1000:10", "y"),
        data,
    )
    text = capture.render_figures_module("Invented data module.\n", SPECS, data)
    namespace: dict[str, object] = {}
    exec(compile(text, "rendered", "exec"), namespace)  # noqa: S102 - our own generated module
    reloaded = capture.load_figures(
        SPECS,
        namespace["FIGURES"],  # type: ignore[arg-type]
        namespace["PROVISIONAL"],  # type: ignore[arg-type]
        namespace["RETRIEVED"],  # type: ignore[arg-type]
    )
    assert reloaded == data
    assert capture.render_figures_module("Invented data module.\n", SPECS, reloaded) == text

    def build(d: capture.FigureData) -> RulePack:
        params, years = capture.pack_parts(SPECS, d)
        return RulePack("t", "xx", 1, params, years, provisional=d.provisional)

    base = build(data).content_hash()
    data.provisional[2027].clear()
    cleared = build(data).content_hash()
    assert cleared != base
    data.figures[2027]["tax.demo.table"] = capture.parse_figure(TABLE, "0:0,1000:11")
    assert build(data).content_hash() != cleared


def test_bump_revision_increments_the_line_and_nothing_else() -> None:
    source = 'X = 1\nREVISION = 9\nREVISION_NOTE = "REVISION = 9 in text"\n'
    new, number = capture.bump_revision(source)
    assert number == 10
    assert new == 'X = 1\nREVISION = 10\nREVISION_NOTE = "REVISION = 9 in text"\n'
    with pytest.raises(RulePackError):
        capture.bump_revision("nothing here")


def test_the_shipped_specs_accept_a_plausible_entry_and_reject_a_slipped_unit() -> None:
    # Property of the declarations, not a figure: dollars-as-cents and percent-as-fraction slip.
    for spec in tax_specs.SPECS:
        assert spec.low < spec.high
    rate = next(s for s in tax_specs.SPECS if s.key == "tax.medicare.levy_rate")
    with pytest.raises(capture.FigureError):
        capture.parse_figure(rate, "$2")
    money = next(s for s in tax_specs.SPECS if s.key == "tax.lito.max")
    with pytest.raises(capture.FigureError):
        capture.parse_figure(money, "99999999")


# --- the script, against a throwaway copy of the pack --------------------------------------

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "rulepack_update.py"


@pytest.fixture
def script_module() -> Iterator[ModuleType]:
    spec = importlib.util.spec_from_file_location("rulepack_update_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["rulepack_update_script"] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop("rulepack_update_script", None)


@pytest.fixture
def pack_copy(tmp_path: Path, script_module: ModuleType) -> Path:
    target = tmp_path / "au"
    target.mkdir()
    for name in ("__init__.py", "tax_figures.py"):
        shutil.copy(script_module.DEFAULT_PACK_DIR / name, target / name)
    return target


def _revision(pack_dir: Path) -> int:
    for line in (pack_dir / "__init__.py").read_text(encoding="utf-8").splitlines():
        if line.startswith("REVISION = "):
            return int(line.removeprefix("REVISION = "))
    raise AssertionError("no REVISION")


def test_the_script_rejects_a_bad_figure_prompts_on_a_jump_and_writes_the_copy(
    script_module: ModuleType, pack_copy: Path
) -> None:
    shipped = script_module.DEFAULT_PACK_DIR
    shipped_before = (shipped / "tax_figures.py").read_text(encoding="utf-8")
    shipped_revision = _revision(shipped)

    # FY 2026: an out-of-range then an off-unit entry are rejected, then 123.45 is accepted.
    first = Script("99999999", "123.45 per fortnight", "123.45", "y", "q")
    code = script_module.run(
        ["--fy", "2026", "--prefix", "tax.lito.max", "--pack-dir", str(pack_copy)],
        first.ask,
        first.say,
        TODAY,
    )
    assert code == 0
    assert first.output.count("Rejected:") == 2
    assert "outside the plausible range" in first.output and "per-fortnight" in first.output
    assert _revision(pack_copy) == shipped_revision + 1

    # FY 2027: 987.65 is a large jump from 123.45; decline it, then 130 goes in provisional.
    second = Script("987.65", "n", "130", "n", "pending the final tables")
    code = script_module.run(
        ["--fy", "2027", "--prefix", "tax.lito.max", "--pack-dir", str(pack_copy)],
        second.ask,
        second.say,
        TODAY,
    )
    assert code == 0
    assert "Last year (2025-26): 123.45" in second.output
    assert "Large change." in second.output
    assert "Provisional" in second.output and "pending the final tables" in second.output
    assert _revision(pack_copy) == shipped_revision + 2

    written = (pack_copy / "tax_figures.py").read_text(encoding="utf-8")
    assert '"tax.lito.max": "123.45"' in written and '"tax.lito.max": "130.00"' in written
    assert '"pending the final tables"' in written and '"2026-11-02"' in written

    # The shipped pack was never touched.
    assert (shipped / "tax_figures.py").read_text(encoding="utf-8") == shipped_before
    assert _revision(shipped) == shipped_revision


def test_the_script_writes_nothing_when_nothing_changes_or_it_is_interrupted(
    script_module: ModuleType, pack_copy: Path
) -> None:
    before = (pack_copy / "tax_figures.py").read_text(encoding="utf-8")
    skip = Script("")
    assert script_module.run(
        ["--fy", "2026", "--prefix", "tax.lito.max", "--pack-dir", str(pack_copy)],
        skip.ask, skip.say, TODAY,
    ) == 0  # fmt: skip
    assert "Nothing changed" in skip.output
    gone = Script()  # no answers: input closes
    assert script_module.run(
        ["--fy", "2026", "--prefix", "tax.lito.max", "--pack-dir", str(pack_copy)],
        gone.ask, gone.say, TODAY,
    ) == 1  # fmt: skip
    assert (pack_copy / "tax_figures.py").read_text(encoding="utf-8") == before
    assert _revision(pack_copy) == _revision(script_module.DEFAULT_PACK_DIR)
    unknown = Script()
    assert script_module.run(
        ["--fy", "2026", "--prefix", "nothing.", "--pack-dir", str(pack_copy)],
        unknown.ask, unknown.say, TODAY,
    ) == 2  # fmt: skip
