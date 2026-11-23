"""Fund-of-funds, pure (ADR-0030 section 5): how references join a Dimension's total, the loop and
depth rules, and the recursive resolver, with goldens worked by hand and Hypothesis invariants.

A held fund's amount is split across its references and its own direct share at every level, so
the lines always sum exactly to the amount, none is negative, and a loop, a chain beyond the cap or
an underlying Instrument with no profile is a No profile line, never a broken total.
"""

from __future__ import annotations

from datetime import date

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from finance360.capabilities.lookthrough.lines import Part, compose
from finance360.capabilities.lookthrough.resolve import MAX_DEPTH, resolve
from finance360.core.exposure import (
    ASSET_CLASS,
    WEIGHT_TOLERANCE_PPM,
    OutsideTolerance,
    ProfileFacts,
    ReferenceCycle,
    ReferencesTooDeep,
    check_references,
    effective_weights,
)
from finance360.core.money import Money
from finance360.core.rate import PPM

ON = date(2026, 9, 30)
AT = date(2026, 6, 30)
ORDER = ["australian_shares", "international_shares", "property", "fixed_income", "cash", "other"]
LABELS = [(k, k.replace("_", " ").capitalize()) for k in ORDER]


def aud(minor: int) -> Money:
    return Money(minor, "AUD")


def fund(
    instrument_id: int,
    references: dict[int, int] | None = None,
    weights: dict[str, int] | None = None,
    as_at: date = AT,
) -> ProfileFacts:
    return ProfileFacts(
        instrument_id * 10,
        instrument_id,
        as_at,
        None,
        "manual",
        tuple((ASSET_CLASS, k, w) for k, w in (weights or {}).items()),
        tuple((references or {}).items()),
    )


def profiles_of(*facts: ProfileFacts) -> dict[int, list[ProfileFacts]]:
    return {f.instrument_id: [f] for f in facts}


def by_label(lines) -> dict[str, int]:  # type: ignore[no-untyped-def]
    out: dict[str, int] = {}
    for line in lines:
        key = line.label_key or line.kind
        out[key] = out.get(key, 0) + line.amount.amount_minor
    return out


# --- a Dimension's total with references ------------------------------------------------------


def test_references_count_towards_the_total_beside_the_direct_weights() -> None:
    got = effective_weights([("cash", 400_000)], ORDER, [(7, 600_000)])
    assert (got.weights, got.references, got.not_classified_ppm) == (
        (("cash", 400_000),),
        ((7, 600_000),),
        0,
    )


def test_references_covering_all_of_it_leave_no_direct_weights() -> None:
    got = effective_weights([], ORDER, [(7, 600_000), (8, 400_000)])
    assert (got.weights, got.references, got.not_classified_ppm) == (
        (),
        ((7, 600_000), (8, 400_000)),
        0,
    )


def test_a_dimension_the_references_only_part_cover_has_the_rest_not_classified() -> None:
    got = effective_weights(
        [], ORDER, [(7, 600_000)]
    )  # a Dimension the fund gives no direct weight
    assert (got.references, got.not_classified_ppm) == (((7, 600_000),), 400_000)


def test_an_excess_with_direct_weights_is_trimmed_from_the_largest_direct_weight() -> None:
    got = effective_weights([("cash", 100_000), ("property", 50_000)], ORDER, [(7, 860_000)])
    assert got.trimmed == (("cash", 10_000),)  # 101%: the references are never the ones trimmed
    assert got.references == ((7, 860_000),) and got.trimmed_references == ()


def test_an_excess_with_no_direct_weights_is_trimmed_from_the_largest_reference() -> None:
    got = effective_weights([], ORDER, [(7, 600_000), (8, 410_000)])  # 101%
    assert got.trimmed_references == ((7, 10_000),)  # 7 holds the most (60%)


def test_a_total_beyond_the_tolerance_is_rejected_with_references_counted() -> None:
    with pytest.raises(OutsideTolerance) as short:
        effective_weights([("cash", 300_000)], ORDER, [(7, 600_000)])  # 90%
    assert short.value.difference_ppm == -100_000
    with pytest.raises(OutsideTolerance):
        effective_weights([], ORDER, [(7, PPM), (8, WEIGHT_TOLERANCE_PPM + 1)])


# --- loops and depth ---------------------------------------------------------------------------


def test_a_fund_cannot_reference_itself() -> None:
    with pytest.raises(ReferenceCycle) as loop:
        check_references({}, 1, [1])
    assert loop.value.loop == (1, 1)


def test_a_loop_through_other_funds_is_named_in_order() -> None:
    edges = {2: {3}, 3: {1}}  # 2 references 3, 3 references 1
    with pytest.raises(ReferenceCycle) as loop:
        check_references(edges, 1, [2])  # writing 1 -> 2 closes 1 > 2 > 3 > 1
    assert loop.value.loop == (1, 2, 3, 1)


def test_a_reference_that_makes_no_loop_is_accepted() -> None:
    check_references({2: {3}, 4: {3}}, 1, [2, 4])  # a diamond is not a loop


def test_the_depth_cap_counts_references_and_judges_the_whole_chain() -> None:
    chain = {i: {i + 1} for i in range(2, MAX_DEPTH + 1)}  # 2 > 3 > ... > MAX_DEPTH+1
    check_references(chain, 1, [2])  # exactly MAX_DEPTH references: allowed
    with pytest.raises(ReferencesTooDeep) as deep:
        check_references({**chain, MAX_DEPTH + 1: {MAX_DEPTH + 2}}, 1, [2])
    assert deep.value.depth == MAX_DEPTH + 1
    # writing in the middle counts the funds above it as well as below
    above = {i: {i + 1} for i in range(1, MAX_DEPTH)}  # 1 > 2 > ... > MAX_DEPTH
    check_references(above, MAX_DEPTH, [900])  # one more reference makes exactly MAX_DEPTH
    with pytest.raises(ReferencesTooDeep):
        check_references({**above, 900: {901}}, MAX_DEPTH, [900])


# --- the recursive resolver --------------------------------------------------------------------


def test_golden_a_two_level_nest_resolves_to_the_underlying_funds() -> None:
    """A holds 50% B and 50% fixed income; B holds 80% C and 20% cash; C is 60/40 shares/property.
    A$1,000.00 of A: B gets 500.00, of which C gets 400.00 (shares 240.00, property 160.00)."""
    a, b, c = 1, 2, 3
    profiles = profiles_of(
        fund(a, {b: 500_000}, {"fixed_income": 500_000}),
        fund(b, {c: 800_000}, {"cash": 200_000}),
        fund(c, None, {"australian_shares": 600_000, "property": 400_000}),
    )
    got = resolve(aud(100_000), a, ASSET_CLASS, ON, profiles, ORDER)
    assert by_label(got.lines) == {
        "fixed_income": 50_000,
        "cash": 10_000,
        "australian_shares": 24_000,
        "property": 16_000,
    }
    paths = {line.label_key: line.path for line in got.lines}
    assert paths == {
        "fixed_income": (a,),
        "cash": (a, b),
        "australian_shares": (a, b, c),
        "property": (a, b, c),
    }
    assert sum(x.amount.amount_minor for x in got.lines) == 100_000
    assert [(v.path, v.amount.amount_minor) for v in got.visits] == [
        ((a, b, c), 40_000),
        ((a, b), 50_000),
    ]


def test_golden_the_fixture_fund_resolves_to_its_two_underlying_funds_and_a_cash_sleeve() -> None:
    """Sample Diversified Fund: 55% Broad Market ETF (60 Australian, 39.5 International, 0.5 not
    classified), 35% Global Equity ETF (99 International, 1 cash) and 10% cash held directly.
    A$1,520.00: 836.00 and 532.00 to the ETFs, 152.00 of cash."""
    diversified, broad, global_ = 1, 2, 3
    profiles = profiles_of(
        fund(diversified, {broad: 550_000, global_: 350_000}, {"cash": 100_000}),
        fund(broad, None, {"australian_shares": 600_000, "international_shares": 395_000}),
        fund(global_, None, {"international_shares": 990_000, "cash": 10_000}),
    )
    got = resolve(aud(152_000), diversified, ASSET_CLASS, ON, profiles, ORDER)
    assert by_label(got.lines) == {
        "australian_shares": 50_160,  # 836.00 x 60%
        "international_shares": 85_690,  # 836.00 x 39.5% = 330.22, 532.00 x 99% = 526.68
        "cash": 15_732,  # 152.00 own + 5.32 from the Global Equity ETF
        "not_classified": 418,  # the Broad Market ETF's 0.5%
    }
    assert sum(x.amount.amount_minor for x in got.lines) == 152_000


def test_a_missing_underlying_profile_is_a_no_profile_line_and_the_total_holds() -> None:
    a, b = 1, 2
    profiles = profiles_of(fund(a, {b: 400_000}, {"cash": 600_000}))  # B has no profile
    got = resolve(aud(10_001), a, ASSET_CLASS, ON, profiles, ORDER)
    missing = [line for line in got.lines if line.kind == "no_profile"]
    assert [(line.path, line.reason, line.amount.amount_minor) for line in missing] == [
        ((a, b), None, 4_000)
    ]
    assert sum(x.amount.amount_minor for x in got.lines) == 10_001


def test_a_loop_met_at_read_is_a_no_profile_line_marked_cycle() -> None:
    a, b = 1, 2
    profiles = profiles_of(
        fund(a, {b: 500_000}, {"cash": 500_000}), fund(b, {a: 500_000}, {"property": 500_000})
    )
    got = resolve(aud(100_000), a, ASSET_CLASS, ON, profiles, ORDER)
    assert by_label(got.lines) == {"cash": 50_000, "property": 25_000, "no_profile": 25_000}
    [loop] = [line for line in got.lines if line.kind == "no_profile"]
    assert (loop.reason, loop.path) == ("cycle", (a, b, a))


def test_a_chain_beyond_the_cap_is_a_no_profile_line_marked_too_deep() -> None:
    last = MAX_DEPTH + 2
    profiles = profiles_of(
        *[fund(i, {i + 1: PPM}) for i in range(1, last)], fund(last, None, {"cash": PPM})
    )
    got = resolve(aud(7_000), 1, ASSET_CLASS, ON, profiles, ORDER)
    [line] = got.lines
    assert (line.kind, line.reason, line.amount.amount_minor) == ("no_profile", "too_deep", 7_000)
    assert line.path == tuple(range(1, MAX_DEPTH + 3))
    # one level shallower resolves
    shallower = profiles_of(
        *[fund(i, {i + 1: PPM}) for i in range(1, last - 1)], fund(last - 1, None, {"cash": PPM})
    )
    [ok] = resolve(aud(7_000), 1, ASSET_CLASS, ON, shallower, ORDER).lines
    assert (ok.kind, ok.label_key, ok.reason) == ("label", "cash", None)


def test_an_excess_over_100_percent_in_the_references_is_trimmed_from_the_largest() -> None:
    a, b, c = 1, 2, 3
    profiles = profiles_of(
        fund(a, {b: 600_000, c: 410_000}),
        fund(b, None, {"cash": PPM}),
        fund(c, None, {"property": PPM}),
    )
    got = resolve(aud(100_000), a, ASSET_CLASS, ON, profiles, ORDER)
    assert got.trimmed_references == ((b, 10_000),)  # B holds the most (60%)
    assert by_label(got.lines) == {"cash": 59_000, "property": 41_000}


# --- the composition names the route ------------------------------------------------------------


def test_the_composition_marks_a_line_an_underlying_fund_gave_to_and_names_the_route() -> None:
    held, under = 1, 2
    profiles = profiles_of(
        fund(held, {under: 500_000}, {"cash": 500_000}), fund(under, None, {"cash": PPM})
    )
    got = compose(
        position_value=aud(80_000),
        parts_value=aud(80_000),
        parts=[Part(held, "Held Fund", aud(80_000))],
        dimension=ASSET_CLASS,
        labels=LABELS,
        on=ON,
        profiles=profiles,
        names_of={under: "Underlying Fund"},
    )
    [cash, unallocated] = got.lines
    assert (cash.kind, cash.amount.amount_minor) == ("label", 80_000)
    assert [(c.path, c.amount.amount_minor) for c in cash.contributions] == [
        (("Held Fund",), 40_000),
        (("Held Fund", "Underlying Fund"), 40_000),
    ]
    assert unallocated.kind == "unallocated" and unallocated.contributions == ()
    [part] = got.parts
    assert [(u.path, u.amount.amount_minor) for u in part.underlying] == [
        (("Held Fund", "Underlying Fund"), 40_000)
    ]


def test_an_ordinary_holding_has_no_contributions() -> None:
    got = compose(
        position_value=aud(1_000),
        parts_value=aud(1_000),
        parts=[Part(1, "Plain", aud(1_000))],
        dimension=ASSET_CLASS,
        labels=LABELS,
        on=ON,
        profiles=profiles_of(fund(1, None, {"cash": PPM})),
    )
    assert all(line.contributions == () for line in got.lines)


# --- invariants (Hypothesis) --------------------------------------------------------------------


@st.composite
def _graphs(draw: st.DrawFn) -> dict[int, list[ProfileFacts]]:
    """Up to five Instruments, each maybe without a profile, referencing any of them (so loops and
    deep chains occur), with direct weights making up the rest within the tolerance."""
    ids = list(range(1, draw(st.integers(1, 5)) + 1))
    profiles: dict[int, list[ProfileFacts]] = {}
    for instrument_id in ids:
        if draw(st.integers(0, 5)) == 0:  # no profile
            continue
        refs = draw(st.lists(st.sampled_from(ids), max_size=3, unique=True))
        if refs:
            cuts = sorted(
                draw(
                    st.lists(
                        st.integers(1, PPM), min_size=len(refs), max_size=len(refs), unique=True
                    )
                )
            )
            weights = [cuts[0], *(b - a for a, b in zip(cuts, cuts[1:], strict=False))]
            assume(all(w > 0 for w in weights))
            referenced = dict(zip(refs, weights, strict=True))
        else:
            referenced = {}
        rest = PPM - sum(referenced.values())
        jitter = draw(st.integers(-WEIGHT_TOLERANCE_PPM, WEIGHT_TOLERANCE_PPM)) if rest else 0
        direct = {}
        if rest + jitter > 0:
            labels = draw(st.lists(st.sampled_from(ORDER), min_size=1, max_size=2, unique=True))
            direct = {labels[0]: rest + jitter}
            if len(labels) == 2 and rest + jitter > 1:
                split = draw(st.integers(1, rest + jitter - 1))
                direct = {labels[0]: rest + jitter - split, labels[1]: split}
        elif not referenced:
            continue
        # a refs-only profile whose references sum to under 100% leaves the rest Not classified
        profiles[instrument_id] = [fund(instrument_id, referenced, direct)]
    return profiles


@given(st.integers(0, 10**10), _graphs(), st.integers(1, 5))
def test_property_a_fund_of_funds_resolution_conserves_the_amount_and_is_never_negative(
    minor: int, profiles: dict[int, list[ProfileFacts]], held: int
) -> None:
    got = resolve(aud(minor), held, ASSET_CLASS, ON, profiles, ORDER)
    assert sum(line.amount.amount_minor for line in got.lines) == minor
    assert all(line.amount.amount_minor > 0 for line in got.lines)  # zero lines are dropped
    assert all(line.path[0] == held and len(line.path) <= MAX_DEPTH + 2 for line in got.lines)


@given(st.integers(0, 10**9), _graphs(), st.integers(1, 5), st.integers(0, 10**9))
def test_property_the_composition_still_reconciles_to_the_position_through_funds(
    minor: int, profiles: dict[int, list[ProfileFacts]], held: int, position_extra: int
) -> None:
    got = compose(
        position_value=aud(minor + position_extra),
        parts_value=aud(minor),
        parts=[Part(held, "Held", aud(minor))],
        dimension=ASSET_CLASS,
        labels=LABELS,
        on=ON,
        profiles=profiles,
    )
    assert sum(line.amount.amount_minor for line in got.lines) == minor + position_extra
    assert all(line.amount.amount_minor >= 0 for line in got.lines if line.kind != "unallocated")
    for line in got.lines:  # a marked line's routes add up to it exactly
        if line.contributions:
            assert (
                sum(c.amount.amount_minor for c in line.contributions) == line.amount.amount_minor
            )
