"""The pure look-through: profile selection, the resolver and the Account composition (ADR-0030).

Golden tests worked by hand, and Hypothesis invariants: the lines always sum exactly to the
Account's Position value, none is negative except Unallocated, and a tolerance rule never loses or
invents a cent.
"""

from __future__ import annotations

from datetime import date

from hypothesis import assume, given
from hypothesis import strategies as st

from finance360.capabilities.lookthrough.lines import Part, compose, percent_text
from finance360.capabilities.lookthrough.reconcile import (
    is_material,
    is_stale_material,
    reconcile_tolerance,
    stale_tolerance,
)
from finance360.capabilities.lookthrough.resolve import resolve, select_profile
from finance360.core.exposure import (
    ASSET_CLASS,
    EXPOSURE_CURRENCY,
    REGION,
    SECTOR,
    WEIGHT_TOLERANCE_PPM,
    ProfileFacts,
)
from finance360.core.money import Money
from finance360.core.rate import PPM

AUD = "AUD"
ON = date(2026, 9, 30)
LABELS = [
    ("australian_shares", "Australian shares"),
    ("international_shares", "International shares"),
    ("property", "Property"),
    ("infrastructure", "Infrastructure"),
    ("fixed_income", "Fixed income"),
    ("cash", "Cash"),
    ("alternatives", "Alternatives"),
    ("other", "Other"),
]
ORDER = [k for k, _ in LABELS]
#: Labels per Dimension (a shortened but representative set for the pure tests).
VOCAB = {
    ASSET_CLASS: LABELS,
    REGION: [
        ("australia", "Australia"),
        ("north_america", "North America"),
        ("europe", "Europe (ex UK)"),
        ("other", "Other"),
    ],
    SECTOR: [
        ("materials", "Materials"),
        ("financials", "Financials"),
        ("information_technology", "Information technology"),
        ("other", "Other"),
    ],
    EXPOSURE_CURRENCY: [("aud", "AUD"), ("usd", "USD"), ("eur", "EUR"), ("other", "Other")],
}


def aud(minor: int) -> Money:
    return Money(minor, AUD)


def profile(
    instrument_id: int,
    as_at: date,
    weights: dict[str, int],
    profile_id: int | None = None,
    dimension: str = ASSET_CLASS,
) -> ProfileFacts:
    return ProfileFacts(
        profile_id or instrument_id * 100 + as_at.day,
        instrument_id,
        as_at,
        None,
        "manual",
        tuple((dimension, k, w) for k, w in weights.items()),
    )


# --- selecting a profile ---------------------------------------------------------------------


def test_golden_the_latest_profile_on_or_before_the_date_is_used() -> None:
    old = profile(1, date(2026, 3, 31), {"cash": PPM})
    new = profile(1, date(2026, 6, 30), {"property": PPM})
    later = profile(1, date(2026, 12, 31), {"fixed_income": PPM})
    got = select_profile([old, later, new], ON)
    assert got is not None and got.profile == new and not got.dated_after
    assert select_profile([old, new], date(2026, 6, 30)).profile == new  # type: ignore[union-attr]


def test_golden_with_none_on_or_before_the_earliest_later_one_is_used_and_marked() -> None:
    a = profile(1, date(2026, 12, 31), {"cash": PPM})
    b = profile(1, date(2027, 6, 30), {"property": PPM})
    got = select_profile([b, a], ON)
    assert got is not None and got.profile == a and got.dated_after


def test_no_profile_at_all_selects_nothing() -> None:
    assert select_profile([], ON) is None


# --- the resolver ----------------------------------------------------------------------------


def test_golden_a_shortfall_goes_to_not_classified_and_the_split_is_exact() -> None:
    profiles = {
        1: [
            profile(
                1,
                date(2026, 6, 30),
                {"australian_shares": 600_000, "international_shares": 395_000},
            )
        ]
    }
    got = resolve(aud(480_538), 1, ASSET_CLASS, ON, profiles, ORDER)
    # 480538 x 60% = 288322.8, x 39.5% = 189812.51, x 0.5% = 2402.69: floors leave 2 minor units,
    # which go to the largest remainders (.8 then .69)
    assert [(line.kind, line.label_key, line.amount.amount_minor) for line in got.lines] == [
        ("label", "australian_shares", 288_323),
        ("label", "international_shares", 189_812),
        ("not_classified", None, 2_403),
    ]
    assert (got.shortfall_ppm, got.trimmed, got.dated_after) == (5_000, (), False)


def test_golden_an_excess_trims_the_largest_and_a_tie_goes_to_the_earliest_label() -> None:
    profiles = {
        1: [
            profile(
                1,
                date(2026, 6, 30),
                {"international_shares": 505_000, "australian_shares": 505_000},
            )
        ]
    }  # 101%: the trim comes off Australian shares (earlier in seed order)
    got = resolve(aud(100_000), 1, ASSET_CLASS, ON, profiles, ORDER)
    assert got.trimmed == (("australian_shares", 10_000),)
    amounts = {line.label_key: line.amount.amount_minor for line in got.lines}
    assert amounts == {"australian_shares": 49_500, "international_shares": 50_500}


def test_a_dimension_the_profile_omits_is_all_not_classified() -> None:
    profiles = {1: [profile(1, date(2026, 6, 30), {})]}
    got = resolve(aud(5_000), 1, ASSET_CLASS, ON, profiles, ORDER)
    assert [(line.kind, line.amount.amount_minor) for line in got.lines] == [
        ("not_classified", 5_000)
    ]


def test_no_profile_is_one_explicit_line_naming_the_instrument() -> None:
    got = resolve(aud(62_500), 7, ASSET_CLASS, ON, {}, ORDER)
    [line] = got.lines
    assert (line.kind, line.amount, line.path) == ("no_profile", aud(62_500), (7,))
    assert got.profile_id is None


def _weights(order: list[str] = ORDER) -> st.SearchStrategy[dict[str, int]]:
    """A profile's weights: 1 to 4 labels totalling 100% plus or minus up to the tolerance."""

    @st.composite
    def build(draw: st.DrawFn) -> dict[str, int]:
        keys = draw(st.lists(st.sampled_from(order), min_size=1, max_size=4, unique=True))
        cuts = sorted(
            draw(
                st.lists(
                    st.integers(1, PPM - 1),
                    min_size=len(keys) - 1,
                    max_size=len(keys) - 1,
                    unique=True,
                )
            )
        )
        edges = [0, *cuts, PPM]
        parts = [edges[i + 1] - edges[i] for i in range(len(keys))]
        parts[0] += draw(st.integers(-WEIGHT_TOLERANCE_PPM, WEIGHT_TOLERANCE_PPM))
        assume(all(p > 0 for p in parts))
        return dict(zip(keys, parts, strict=True))

    return build()


@given(st.integers(0, 10**10), _weights())
def test_property_a_resolved_split_sums_exactly_and_has_no_negative_line(
    minor: int, weights: dict[str, int]
) -> None:
    profiles = {1: [profile(1, date(2026, 6, 30), weights)]}
    got = resolve(aud(minor), 1, ASSET_CLASS, ON, profiles, ORDER)
    assert sum(line.amount.amount_minor for line in got.lines) == minor
    assert all(line.amount.amount_minor > 0 for line in got.lines)  # zero lines are dropped


# --- the Account composition -----------------------------------------------------------------

#: The synthetic sample Account on 2026-09-30, by hand: the ETF (profile totalling 99.5%), the
#: mining share (100% Australian shares), the new ETF (no profile) and cash.
SAMPLE_PARTS = [
    Part(1, "Sample Broad Market ETF", aud(480_538)),
    Part(2, "Sample Mining Ltd", aud(37_982)),
    Part(3, "Sample New Horizons ETF", aud(62_500)),
    Part(None, "Cash (AUD)", aud(83_493)),
]
SAMPLE_PROFILES = {
    1: [
        profile(
            1, date(2026, 6, 30), {"australian_shares": 600_000, "international_shares": 395_000}
        )
    ],
    2: [profile(2, date(2026, 6, 30), {"australian_shares": PPM})],
}


def _compose(position: int, parts_value: int, parts: list[Part] = SAMPLE_PARTS):  # type: ignore[no-untyped-def]
    return compose(
        position_value=aud(position),
        parts_value=aud(parts_value),
        parts=parts,
        dimension=ASSET_CLASS,
        labels=LABELS,
        on=ON,
        profiles=SAMPLE_PROFILES,
    )


def test_golden_the_sample_account_splits_by_asset_class_and_reconciles_to_the_position() -> None:
    got = _compose(664_513, 664_513)
    assert [
        (line.kind, line.name, line.amount.amount_minor, line.percent) for line in got.lines
    ] == [
        ("label", "Australian shares", 326_305, "49.10"),  # 288323 + 37982
        ("label", "International shares", 189_812, "28.56"),
        ("label", "Cash", 83_493, "12.56"),
        ("no_profile", "No profile: Sample New Horizons ETF", 62_500, "9.41"),
        ("not_classified", "Not classified", 2_403, "0.36"),
        ("unallocated", "Unallocated", 0, "0.00"),
    ]
    assert sum(line.amount.amount_minor for line in got.lines) == 664_513
    assert not got.flagged
    [etf, *_] = got.parts
    assert (etf.shortfall_ppm, etf.profile_as_at) == (5_000, date(2026, 6, 30))
    no_profile = next(line for line in got.lines if line.kind == "no_profile")
    assert no_profile.instrument_id == 3


def test_golden_a_gap_between_the_position_and_the_parts_is_unallocated_and_never_scaled() -> None:
    got = _compose(700_000, 664_513)
    unallocated = got.lines[-1]
    assert (unallocated.kind, unallocated.amount.amount_minor) == ("unallocated", 35_487)
    # 5.07% of the value, beyond the larger of 1% (7,000) and A$50 (5,000): flagged
    assert got.flagged
    assert got.lines[0].amount.amount_minor == 326_305  # the parts keep their values
    assert sum(line.amount.amount_minor for line in got.lines) == 700_000


def test_golden_over_allocation_is_a_negative_unallocated_line_and_still_reconciles() -> None:
    got = _compose(600_000, 664_513)
    assert got.lines[-1].amount.amount_minor == -64_513
    assert got.flagged
    assert sum(line.amount.amount_minor for line in got.lines) == 600_000


def test_a_gap_within_the_tolerance_is_shown_quietly_of_either_sign() -> None:
    assert not _compose(664_513 + 4_000, 664_513).flagged  # under both 1% (6,645) and A$50
    assert not _compose(664_513 - 4_000, 664_513).flagged
    assert _compose(664_513 + 7_000, 664_513).flagged  # beyond 1%


def test_reconcile_tolerance_is_the_larger_of_one_percent_and_fifty_dollars() -> None:
    assert reconcile_tolerance(aud(100_000)) == aud(5_000)  # 1% is 1,000: the floor wins
    assert reconcile_tolerance(aud(10_000_000)) == aud(100_000)  # 1% is 100,000 minor
    assert not is_material(aud(5_000), aud(100_000)) and is_material(aud(5_001), aud(100_000))
    assert is_material(aud(-5_001), aud(100_000))


def test_no_parts_leaves_everything_unallocated() -> None:
    got = _compose(10_000, 0, [])
    assert [(line.kind, line.amount.amount_minor) for line in got.lines] == [
        ("unallocated", 10_000)
    ]


def test_percent_text_rounds_once_from_money() -> None:
    assert percent_text(aud(1), aud(3)) == "33.33"
    assert percent_text(aud(2), aud(3)) == "66.67"
    assert percent_text(aud(-1), aud(3)) == "-33.33"
    assert percent_text(aud(1), aud(0)) is None


@st.composite
def _scenarios(draw: st.DrawFn):  # type: ignore[no-untyped-def]
    n = draw(st.integers(0, 5))
    amounts = draw(st.lists(st.integers(0, 10**8), min_size=n, max_size=n))
    dimension = draw(st.sampled_from(sorted(VOCAB)))
    order = [k for k, _ in VOCAB[dimension]]
    parts = [
        Part(
            None if i == 0 and draw(st.booleans()) else i + 1,
            f"Part {i}",
            aud(a),
            "AUD" if i == 0 else None,
        )
        for i, a in enumerate(amounts)
    ]
    profiles = {}
    for part in parts:
        if part.instrument_id is not None and draw(st.booleans()):
            # a profile gives this Dimension weights, or omits it (it weights another one only)
            elsewhere = draw(st.booleans())
            other = next(d for d in sorted(VOCAB) if d != dimension)
            profiles[part.instrument_id] = [
                profile(
                    part.instrument_id,
                    date(2026, 6, 30),
                    {VOCAB[other][0][0]: PPM} if elsewhere else draw(_weights(order)),
                    dimension=other if elsewhere else dimension,
                )
            ]
    return (
        dimension,
        parts,
        profiles,
        draw(st.integers(0, 2 * 10**9)),
        draw(st.integers(0, 2 * 10**9)),
    )


@given(_scenarios())
def test_property_every_dimension_sums_exactly_to_the_position_value_and_none_is_negative(
    scenario,  # type: ignore[no-untyped-def]
) -> None:
    dimension, parts, profiles, position, parts_value = scenario
    got = compose(
        position_value=aud(position),
        parts_value=aud(parts_value),
        parts=parts,
        dimension=dimension,
        labels=VOCAB[dimension],
        on=ON,
        profiles=profiles,
    )
    assert sum(line.amount.amount_minor for line in got.lines) == position
    assert all(line.amount.amount_minor >= 0 for line in got.lines if line.kind != "unallocated")
    analysed = sum(part.amount.amount_minor for part in got.parts)
    assert got.unallocated.amount_minor == position - analysed
    assert analysed in (0, parts_value)


def test_golden_parts_in_another_currency_are_allocated_across_the_converted_total() -> None:
    """A GBP Account: the parts are in GBP, the Position value and the converted total are AUD.
    The AUD parts are the converted total allocated across the GBP parts, so they sum exactly to
    it, and what the Position adds beyond it is Unallocated."""
    parts = [
        Part(2, "Sample Mining Ltd", Money(70_001, "GBP")),
        Part(None, "Cash (GBP)", Money(29_999, "GBP")),
    ]
    got = compose(
        position_value=aud(210_000),
        parts_value=aud(200_001),  # 100,000 GBP converted at an invented rate, rounded once
        parts=parts,
        dimension=ASSET_CLASS,
        labels=LABELS,
        on=ON,
        profiles=SAMPLE_PROFILES,
    )
    assert [p.amount.amount_minor for p in got.parts] == [140_003, 59_998]
    assert sum(p.amount.amount_minor for p in got.parts) == 200_001
    assert [(line.kind, line.amount.amount_minor) for line in got.lines] == [
        ("label", 140_003),  # Australian shares
        ("label", 59_998),  # Cash
        ("unallocated", 9_999),
    ]
    assert all(line.amount.currency == "AUD" for line in got.lines)


# --- the other Dimensions (#84) --------------------------------------------------------------

#: An AUD-listed global ETF (invented): AUD Holding currency, mostly non-AUD Exposure currency,
#: and no sector weights at all; a share with region and sector but no Exposure currency.
GLOBAL_ETF = ProfileFacts(
    1,
    1,
    date(2026, 6, 30),
    None,
    "manual",
    (
        (REGION, "australia", 100_000),
        (REGION, "north_america", 600_000),
        (REGION, "europe", 300_000),
        (EXPOSURE_CURRENCY, "aud", 100_000),
        (EXPOSURE_CURRENCY, "usd", 700_000),
        (EXPOSURE_CURRENCY, "eur", 200_000),
    ),
)
SHARE = ProfileFacts(
    2,
    2,
    date(2026, 6, 30),
    None,
    "manual",
    ((REGION, "australia", PPM), (SECTOR, "materials", PPM)),
)
OTHER_PARTS = [
    Part(1, "Sample Global ETF", aud(100_000)),
    Part(2, "Sample Mining Ltd", aud(40_000)),
    Part(None, "Cash (AUD)", aud(10_000), "AUD"),
]


def _by(dimension: str):  # type: ignore[no-untyped-def]
    return compose(
        position_value=aud(150_000),
        parts_value=aud(150_000),
        parts=OTHER_PARTS,
        dimension=dimension,
        labels=VOCAB[dimension],
        on=ON,
        profiles={1: [GLOBAL_ETF], 2: [SHARE]},
    )


def test_golden_an_aud_etf_is_mostly_non_aud_by_exposure_currency() -> None:
    got = _by(EXPOSURE_CURRENCY)
    rows = [(line.kind, line.name, line.amount.amount_minor) for line in got.lines]
    assert rows == [
        ("label", "AUD", 20_000),  # 10% of the ETF's 100,000, plus the 10,000 of cash
        ("label", "USD", 70_000),
        ("label", "EUR", 20_000),
        ("not_classified", "Not classified", 40_000),  # the share gives no Exposure currency
        ("unallocated", "Unallocated", 0),
    ]
    assert sum(amount for *_, amount in rows) == 150_000


def test_golden_cash_is_its_own_currency_and_not_classified_in_region_and_sector() -> None:
    region = {line.name: line.amount.amount_minor for line in _by(REGION).lines}
    assert region["Not classified"] == 10_000  # cash has no region
    assert region["Australia"] == 10_000 + 40_000
    sector = {line.name: line.amount.amount_minor for line in _by(SECTOR).lines}
    assert sector["Materials"] == 40_000
    assert sector["Not classified"] == 100_000 + 10_000  # the ETF omits sectors; cash has none


def test_a_part_whose_profile_omits_the_dimension_is_marked_omitted() -> None:
    sector = _by(SECTOR)
    assert [(p.name, p.omitted) for p in sector.parts] == [
        ("Sample Global ETF", True),
        ("Sample Mining Ltd", False),
        ("Cash (AUD)", False),
    ]
    assert not any(p.omitted for p in _by(REGION).parts if p.instrument_id == 1)


def test_cash_in_a_currency_the_vocabulary_does_not_list_is_not_classified() -> None:
    parts = [Part(None, "Cash (BHD)", Money(5_000, "BHD"), "BHD")]
    got = compose(
        position_value=aud(5_000),
        parts_value=aud(5_000),
        parts=parts,
        dimension=EXPOSURE_CURRENCY,
        labels=VOCAB[EXPOSURE_CURRENCY],
        on=ON,
        profiles={},
    )
    assert [(line.kind, line.amount.amount_minor) for line in got.lines] == [
        ("not_classified", 5_000),
        ("unallocated", 0),
    ]


# --- super Investment options (#86) ------------------------------------------------------------


def _options(amounts: list[int], mapped: list[bool]) -> list[Part]:
    """Option parts: a mapped one resolves through Instrument 1 or 2 (alternating), an unmapped
    one has no Instrument."""
    return [
        Part((i % 2) + 1 if m else None, f"Option {i}", aud(a), option=f"Option {i}")
        for i, (a, m) in enumerate(zip(amounts, mapped, strict=True))
    ]


def test_golden_an_unmapped_option_is_a_no_profile_line_naming_the_option() -> None:
    parts = _options([60_000, 40_000], [True, False])
    got = compose(
        position_value=aud(110_000),
        parts_value=aud(100_000),
        parts=parts,
        dimension=ASSET_CLASS,
        labels=LABELS,
        on=ON,
        profiles=SAMPLE_PROFILES,
    )
    unmapped = next(line for line in got.lines if line.option_name == "Option 1")
    assert (unmapped.kind, unmapped.instrument_id, unmapped.amount.amount_minor) == (
        "no_profile",
        None,
        40_000,
    )
    assert [(p.option, p.instrument_id) for p in got.parts] == [("Option 0", 1), ("Option 1", None)]
    assert got.unallocated.amount_minor == 10_000
    assert sum(line.amount.amount_minor for line in got.lines) == 110_000


def test_golden_a_stale_mix_is_judged_against_fifteen_percent_and_says_since_when() -> None:
    """A 12% gap: beyond the 1% reconcile tolerance, within the 15% stale one."""
    kwargs = dict(
        position_value=aud(1_000_000),
        parts_value=aud(880_000),
        parts=_options([880_000], [True]),
        dimension=ASSET_CLASS,
        labels=LABELS,
        on=ON,
        profiles=SAMPLE_PROFILES,
    )
    plain = compose(**kwargs)  # type: ignore[arg-type]
    stale = compose(**kwargs, change_since=date(2026, 6, 30))  # type: ignore[arg-type]
    assert (plain.flagged, plain.change_since) == (True, None)
    assert (stale.flagged, stale.change_since) == (False, date(2026, 6, 30))
    assert stale.tolerance.amount_minor == 150_000
    beyond = compose(**{**kwargs, "parts_value": aud(840_000)}, change_since=date(2026, 6, 30))  # type: ignore[arg-type]
    assert beyond.flagged is True  # 16%: "the option mix may have changed"


def test_the_stale_tolerance_is_fifteen_percent_of_the_value_for_either_sign() -> None:
    assert stale_tolerance(aud(1_000_000)).amount_minor == 150_000
    assert is_stale_material(aud(150_000), aud(1_000_000)) is False
    assert is_stale_material(aud(150_001), aud(1_000_000)) is True
    assert is_stale_material(aud(-150_001), aud(1_000_000)) is True


@given(
    st.lists(st.tuples(st.integers(1, 10**9), st.booleans()), max_size=6),
    st.integers(-(10**10), 10**10),
    st.one_of(st.none(), st.just(date(2026, 6, 30))),
)
def test_property_option_parts_plus_unallocated_equal_the_account_value_for_any_sign(
    options: list[tuple[int, bool]], gap: int, since: date | None
) -> None:
    """Parts never scale: the lines are the parts' values plus Unallocated (either sign), which
    always makes up the Account value exactly; nothing but Unallocated is negative."""
    amounts = [a for a, _ in options]
    total = sum(amounts)
    position = total + gap
    assume(position > 0)
    got = compose(
        position_value=aud(position),
        parts_value=aud(total),
        parts=_options(amounts, [m for _, m in options]),
        dimension=ASSET_CLASS,
        labels=LABELS,
        on=ON,
        profiles=SAMPLE_PROFILES,
        change_since=since,
    )
    assert sum(line.amount.amount_minor for line in got.lines) == position
    assert [p.amount.amount_minor for p in got.parts] == amounts  # never scaled
    assert got.unallocated.amount_minor == gap
    assert all(line.amount.amount_minor >= 0 for line in got.lines if line.kind != "unallocated")
