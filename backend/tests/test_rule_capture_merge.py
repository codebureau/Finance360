"""The pure merge of captured figures over a Rule pack (ADR-0022): ``layer`` and ``merge``.

Invariant work (figures used in money maths): each test names the defect it would catch. Every value
is invented; the specs are small, obviously fake declarations.
"""

from __future__ import annotations

from datetime import date, datetime

from hypothesis import given
from hypothesis import strategies as st

from finance360.core.money import Money
from finance360.core.rate import Rate
from finance360.core.rulepack import (
    CPI,
    FROZEN,
    MONEY,
    RATE,
    Capture,
    FigureSpec,
    RulePack,
    capture_digest,
    layer,
    merge,
    value_json,
)

CENTS = "AUD"
NOW = datetime(2026, 11, 5, 1, 0, 0)
DAY = date(2026, 11, 5)

# A money-per-km figure the pack holds for two years, a share it holds for one, and a third key that
# is declared for capture but holds no figure yet.
KM = FigureSpec(
    "tax.demo.per_km", MONEY, FROZEN, "Demo rate", "Invented source", "https://example.invalid/a",
    "Box A.", 0, 5_000, "km",
)  # fmt: skip
SHARE = FigureSpec(
    "tax.demo.share", RATE, CPI, "Demo share", "Invented source", "https://example.invalid/b",
    "Box B.", 0, 400_000,
)  # fmt: skip
NEW = FigureSpec(
    "tax.demo.new_cap", MONEY, FROZEN, "Demo cap", "Invented source", "https://example.invalid/c",
    "Box C.", 0, 900_000, "year",
)  # fmt: skip

SHIPPED = RulePack(
    pack_id="demo",
    jurisdiction="demo",
    revision=3,
    params=(KM.to_param(), SHARE.to_param()),
    years={
        2025: {KM.key: Money(80, CENTS), SHARE.key: Rate(100_000)},
        2026: {KM.key: Money(85, CENTS), SHARE.key: Rate(110_000)},
    },
    declared=(KM.to_param(), SHARE.to_param(), NEW.to_param()),
    specs=(KM, SHARE, NEW),
)


def capture(
    id_: int,
    key: str,
    fy: int,
    value: Money | Rate,
    *,
    note: str | None = None,
    quote: str | None = "An invented sentence.",
) -> Capture:
    return Capture(
        id_, key, fy, value_json(value), note, quote, "https://example.invalid/p", DAY, NOW, "owner"
    )


def test_no_captures_is_the_shipped_pack_itself_with_an_unchanged_label() -> None:
    # Catches: a layer that rebuilds the pack, or suffixes the label, with nothing to layer.
    assert layer(SHIPPED, []) is SHIPPED
    assert SHIPPED.version_label == "demo/2025-26/r3"
    assert SHIPPED.captured == () and SHIPPED.effective_hash() == SHIPPED.content_hash()


def test_a_capture_overrides_exactly_its_key_and_year() -> None:
    # Catches: a merge that touches another year, another key, or the shipped pack itself.
    merged = layer(SHIPPED, [capture(7, KM.key, 2026, Money(90, CENTS))])
    assert merged.years[2026][KM.key] == Money(90, CENTS)
    assert merged.years[2026][SHARE.key] == SHIPPED.years[2026][SHARE.key]
    assert merged.years[2025] == SHIPPED.years[2025]
    assert SHIPPED.years[2026][KM.key] == Money(85, CENTS)  # the shipped pack is never mutated
    (figure,) = merged.captured
    assert (figure.id, figure.key, figure.fy) == (7, KM.key, 2026)
    assert figure.shipped_value == Money(85, CENTS)
    assert merged.capture_of(2026, KM.key) is figure and merged.capture_of(2025, KM.key) is None


def test_a_capture_for_a_new_year_adds_it_and_moves_the_label_year() -> None:
    # Catches: a capture for a year the pack lacks being dropped, or the label keeping the old year.
    merged = layer(SHIPPED, [capture(1, KM.key, 2027, Money(90, CENTS))])
    assert merged.years[2027] == {KM.key: Money(90, CENTS)}
    assert merged.last_fy == 2027
    assert merged.version_label.startswith("demo/2026-27/r3+1:")
    assert merged.capture_of(2027, KM.key).shipped_value is None  # type: ignore[union-attr]


def test_a_declared_key_with_no_figure_joins_the_pack_from_its_spec() -> None:
    merged = layer(SHIPPED, [capture(1, NEW.key, 2026, Money(900, CENTS))])
    assert merged.param(NEW.key).unit == "year"
    assert merged.years[2026][NEW.key] == Money(900, CENTS)


def test_a_capture_the_shipped_figure_already_holds_is_redundant() -> None:
    # Catches: a redundant capture changing the label (so two runs of identical numbers differ).
    same = capture(1, KM.key, 2026, Money(85, CENTS))
    result = merge(SHIPPED, [same])
    assert result.pack is SHIPPED and result.redundant == (same,) and result.applied == ()
    assert result.pack.version_label == SHIPPED.version_label


def test_a_provisional_capture_loses_to_a_firm_shipped_figure() -> None:
    announced = capture(1, KM.key, 2026, Money(99, CENTS), note="Announced, not yet law.")
    result = merge(SHIPPED, [announced])
    assert result.pack is SHIPPED and result.redundant == (announced,)


def test_a_provisional_capture_marks_its_figure_provisional_and_a_firm_one_clears_it() -> None:
    announced = capture(1, KM.key, 2027, Money(99, CENTS), note="Announced, not yet law.")
    merged = layer(SHIPPED, [announced])
    assert merged.provisional_note(2027, KM.key) == "Announced, not yet law."
    firm = layer(merged, [capture(2, KM.key, 2027, Money(99, CENTS))])
    assert firm.provisional_note(2027, KM.key) is None


def test_a_capture_that_no_longer_fits_is_ignored_and_reported_never_raised() -> None:
    # Catches: a bad stored row stopping the app (the merge must never raise for a capture).
    undeclared = capture(1, "tax.demo.gone", 2026, Money(5, CENTS))
    out_of_range = capture(2, KM.key, 2026, Money(5_001, CENTS))
    wrong_kind = capture(3, SHARE.key, 2026, Money(5, CENTS))
    too_early = capture(4, KM.key, 2000, Money(5, CENTS))
    result = merge(SHIPPED, [undeclared, out_of_range, wrong_kind, too_early])
    assert result.pack is SHIPPED and result.applied == ()
    assert [c.id for c, _ in result.unfit] == [4, 1, 2, 3]  # in (year, key, id) order
    assert all(reason for _, reason in result.unfit)
    good = capture(5, KM.key, 2027, Money(90, CENTS))
    mixed = merge(SHIPPED, [undeclared, good])
    assert mixed.pack.years[2027][KM.key] == Money(90, CENTS) and len(mixed.unfit) == 1


def test_the_label_digest_follows_the_captures_and_is_stable_for_the_same_set() -> None:
    # Catches: a digest that ignores a value or a note, or depends on order or capture ids.
    a = capture(1, KM.key, 2027, Money(90, CENTS))
    b = capture(2, SHARE.key, 2027, Rate(120_000))
    label = layer(SHIPPED, [a, b]).version_label
    assert label.startswith("demo/2026-27/r3+2:") and len(label.split(":")[1]) == 6
    assert layer(SHIPPED, [b, a]).version_label == label  # order does not matter
    renumbered = [
        capture(40, KM.key, 2027, Money(90, CENTS)),
        capture(41, SHARE.key, 2027, Rate(120_000)),
    ]
    assert layer(SHIPPED, renumbered).version_label == label  # ids are not in the digest
    changed = [capture(1, KM.key, 2027, Money(91, CENTS)), b]
    noted = [capture(1, KM.key, 2027, Money(90, CENTS), note="Announced."), b]
    withdrawn = [b]
    labels = {label}
    for captures in (changed, noted, withdrawn):
        labels.add(layer(SHIPPED, captures).version_label)
    assert len(labels) == 4  # every change moves it
    assert layer(SHIPPED, [a, b]).effective_hash() != SHIPPED.content_hash()
    assert layer(SHIPPED, [a, b]).effective_hash() == layer(SHIPPED, [b, a]).effective_hash()


def test_the_shipped_content_hash_is_untouched_by_layering() -> None:
    before = SHIPPED.content_hash()
    layer(SHIPPED, [capture(1, KM.key, 2027, Money(90, CENTS))])
    assert SHIPPED.content_hash() == before


@given(
    cents=st.integers(min_value=0, max_value=5_000),
    fy=st.integers(min_value=2025, max_value=2030),
    seed=st.integers(min_value=0, max_value=1_000),
)
def test_property_a_capture_changes_only_its_own_key_and_year(
    cents: int, fy: int, seed: int
) -> None:
    captures = [
        capture(1, KM.key, fy, Money(cents, CENTS)),
        capture(2, SHARE.key, 2028, Rate(seed * 100)),
    ]
    shuffled = list(reversed(captures))
    result = merge(SHIPPED, captures)
    again = merge(SHIPPED, shuffled)
    assert result.pack.version_label == again.pack.version_label
    for year, values in result.pack.years.items():
        for key, value in values.items():
            hit = (year, key) in {(fy, KM.key), (2028, SHARE.key)}
            if not hit:
                assert value == SHIPPED.years[year][key]
    if SHIPPED.years.get(fy, {}).get(KM.key) == Money(cents, CENTS):
        assert 1 not in {c.id for c in result.applied}  # redundant: it does not count
    else:
        assert result.pack.years[fy][KM.key] == Money(cents, CENTS)


def test_the_digest_function_is_canonical_over_value_and_note_only() -> None:
    one = capture(1, KM.key, 2027, Money(90, CENTS))
    other_source = Capture(
        9, KM.key, 2027, value_json(Money(90, CENTS)), None, "Other", "x", DAY, NOW, "agent"
    )
    assert capture_digest([one]) == capture_digest([other_source])
