"""The pure Position calculation: worked examples (unit) and invariants (Hypothesis).

Nothing here touches a database: the calculation takes immutable inputs. The sample household's
hand-calculated answers live in ``test_position_golden``.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from finance360.capabilities.position.calculation import (
    AccountFacts,
    AccountLine,
    CategoryInfo,
    Figures,
    Position,
    compute_position,
    counts_on,
)
from finance360.core.fx import FxPoint
from finance360.core.money import Money
from finance360.core.valuation import SOURCES, ValuationPoint

CATEGORIES = (
    CategoryInfo("cash", "Cash", "asset"),
    CategoryInfo("property", "Property", "asset"),
    CategoryInfo("loans", "Loans", "liability"),
)
D = date(2026, 6, 30)


def aud(minor: int) -> Money:
    return Money(minor, "AUD")


def val(
    id_: int, day: date, minor: int, ccy: str = "AUD", source: str = "statement"
) -> ValuationPoint:
    return ValuationPoint(id_, day, source, Money(minor, ccy))


def acct(
    id_: int,
    category: str = "cash",
    *,
    valuations: tuple[ValuationPoint, ...] = (),
    owners: tuple[tuple[int, int], ...] = ((1, 10000),),
    currency: str = "AUD",
    tags: tuple[str, ...] = (),
    opened_on: date | None = None,
    closed_on: date | None = None,
) -> AccountFacts:
    return AccountFacts(
        id=id_,
        name=f"Account {id_}",
        category_key=category,
        currency=currency,
        ownership="joint" if len(owners) > 1 else "sole",
        owners=owners,
        tags=tags,
        opened_on=opened_on,
        closed_on=closed_on,
        valuations=valuations,
    )


def position(
    accounts: list[AccountFacts],
    *,
    on: date = D,
    fx: list[FxPoint] | None = None,
    tag: str | None = None,
    members: tuple[int, ...] = (1, 2),
) -> Position:
    return compute_position(
        on=on, accounts=accounts, categories=CATEGORIES, member_ids=members, fx=fx or [], tag=tag
    )


def lines(p: Position) -> list[AccountLine]:
    return [a for c in p.classes for k in c.categories for a in k.accounts]


def total(values: list[Money | None]) -> Money:
    return sum((v for v in values if v is not None), aud(0))


# --- worked examples -----------------------------------------------------------------------


def test_a_liability_is_a_positive_amount_owed_that_the_class_makes_negative() -> None:
    p = position(
        [
            acct(1, valuations=(val(1, D, 1_000_00),)),
            acct(2, "loans", valuations=(val(2, D, 400_00),)),
        ]
    )
    assert [c.figures.household for c in p.classes] == [aud(1_000_00), aud(-400_00)]
    assert lines(p)[1].original == aud(400_00)  # shown as recorded: the amount owed
    assert lines(p)[1].value == aud(-400_00)
    assert p.net.household == aud(600_00)  # Assets minus Liabilities


def test_the_best_valuation_on_or_before_the_date_wins_and_later_ones_are_ignored() -> None:
    a = acct(
        1,
        valuations=(
            val(1, date(2026, 5, 31), 100_00),
            val(2, date(2026, 6, 30), 150_00),
            val(3, date(2026, 7, 31), 999_00),
        ),
    )
    assert position([a]).net.household == aud(150_00)
    assert position([a], on=date(2026, 6, 29)).net.household == aud(100_00)


def test_foreign_currency_is_converted_at_the_rate_on_or_before_the_date() -> None:
    rates = [
        FxPoint(1, "GBP", "AUD", date(2026, 1, 1), Decimal("2.0000")),
        FxPoint(2, "GBP", "AUD", date(2026, 6, 1), Decimal("2.0143")),
        FxPoint(3, "GBP", "AUD", date(2026, 7, 1), Decimal("9.0000")),
    ]
    a = acct(1, currency="GBP", valuations=(val(1, D, 100_00, "GBP"),))
    [line] = lines(position([a], fx=rates))
    assert (line.rate, line.rate_as_at) == (Decimal("2.0143"), date(2026, 6, 1))
    assert line.value == aud(201_43)  # 100.00 GBP x 2.0143


def test_the_line_names_the_source_of_the_rate_and_a_manual_rate_wins_a_same_date_tie() -> None:
    rates = [
        FxPoint(1, "GBP", "AUD", date(2026, 6, 1), Decimal("2.0143"), "manual"),
        FxPoint(2, "GBP", "AUD", date(2026, 6, 1), Decimal("2.5000"), "rba"),  # entered later
    ]
    a = acct(1, currency="GBP", valuations=(val(1, D, 100_00, "GBP"),))
    [line] = lines(position([a], fx=rates))
    assert (line.rate_source, line.rate) == ("manual", Decimal("2.0143"))
    assert line.value == aud(201_43)
    newer = [*rates, FxPoint(3, "GBP", "AUD", date(2026, 6, 2), Decimal("2.1000"), "rba")]
    [line] = lines(position([a], fx=newer))
    assert (line.rate_source, line.rate_as_at) == ("rba", date(2026, 6, 2))  # newer beats manual
    [aud_line] = lines(position([acct(2, valuations=(val(2, D, 5_00),))]))
    assert aud_line.rate_source is None  # an AUD Account has no rate


def test_no_valuation_is_flagged_and_left_out_of_every_total_not_zeroed() -> None:
    p = position([acct(1, valuations=(val(1, D, 50_00),)), acct(2, valuations=())])
    flagged = lines(p)[1]
    assert (flagged.status, flagged.value, flagged.shares) == ("no_valuation", None, ())
    assert p.net.household == aud(50_00)
    assert (p.flagged, p.classes[0].flagged, p.classes[0].categories[0].flagged) == (1, 1, 1)


def test_a_valuation_dated_after_the_date_does_not_count_either() -> None:
    p = position([acct(1, valuations=(val(1, D + timedelta(days=1), 50_00),))])
    assert (lines(p)[0].status, p.flagged) == ("no_valuation", 1)


def test_no_fx_rate_is_flagged_never_assumed_one_and_keeps_the_recorded_figure() -> None:
    other_pair = [FxPoint(1, "USD", "AUD", date(2026, 1, 1), Decimal("1.5"))]
    later_only = [FxPoint(2, "GBP", "AUD", D + timedelta(days=1), Decimal("2"))]
    a = acct(1, currency="GBP", valuations=(val(1, D, 100_00, "GBP"),))
    for fx in ([], other_pair, later_only):
        p = position([a], fx=fx)
        [line] = lines(p)
        assert (line.status, line.value, line.original) == ("no_rate", None, Money(100_00, "GBP"))
        assert (p.net.household, p.flagged) == (aud(0), 1)


def test_an_account_counts_iff_opened_on_le_d_lt_closed_on() -> None:
    a = acct(
        1,
        valuations=(val(1, date(2020, 1, 1), 10_00),),
        opened_on=date(2026, 3, 1),
        closed_on=date(2026, 9, 1),
    )
    assert not counts_on(a, date(2026, 2, 28))
    assert counts_on(a, date(2026, 3, 1))  # opened_on is inclusive
    assert counts_on(a, date(2026, 8, 31))
    assert not counts_on(a, date(2026, 9, 1))  # closed_on is the first date it no longer counts
    assert lines(position([a], on=date(2026, 8, 31)))[0].value == aud(10_00)
    assert lines(position([a], on=date(2026, 9, 1))) == []  # absent, not flagged or zero
    assert lines(position([a], on=date(2026, 2, 28))) == []


def test_open_ended_bounds_are_null() -> None:
    a = acct(1, valuations=(val(1, date(2000, 1, 1), 1_00),))
    assert counts_on(a, date(1990, 1, 1)) and counts_on(a, date(2999, 1, 1))


def test_an_odd_minor_unit_goes_to_the_lowest_member_id() -> None:
    a = acct(1, valuations=(val(1, D, 100_01),), owners=((2, 5000), (1, 5000)))
    [line] = lines(position([a]))
    assert dict(line.shares) == {1: aud(50_01), 2: aud(50_00)}  # 100.01 / 2: the spare cent -> 1
    f = position([a]).net
    assert (f.joint, f.household) == (aud(100_01), aud(100_01))
    assert [(m.member_id, m.sole, m.joint_share) for m in f.members] == [
        (1, aud(0), aud(50_01)),
        (2, aud(0), aud(50_00)),
    ]


def test_uneven_three_way_shares_sum_exactly() -> None:
    a = acct(1, valuations=(val(1, D, 100_00),), owners=((1, 3333), (2, 3333), (3, 3334)))
    [line] = lines(position([a], members=(1, 2, 3)))
    # 10000 cents x 3333/10000 = 3333 and x 3334/10000 = 3334, exactly, so no stray cents
    assert dict(line.shares) == {1: aud(33_33), 2: aud(33_33), 3: aud(33_34)}


def test_a_liability_split_is_symmetric() -> None:
    a = acct(1, "loans", valuations=(val(1, D, 100_01),), owners=((1, 5000), (2, 5000)))
    [line] = lines(position([a]))
    assert dict(line.shares) == {1: aud(-50_01), 2: aud(-50_00)}


def test_member_view_sole_plus_joint_share_and_joint_shown_separately() -> None:
    p = position(
        [
            acct(1, valuations=(val(1, D, 10_00),), owners=((1, 10000),)),
            acct(2, valuations=(val(2, D, 20_00),), owners=((2, 10000),)),
            acct(3, "property", valuations=(val(3, D, 40_00),), owners=((1, 5000), (2, 5000))),
        ]
    )
    f = p.net
    assert f.household == aud(70_00)
    assert f.joint == aud(40_00)
    by = {m.member_id: m for m in f.members}
    assert (by[1].sole, by[1].joint_share, by[1].total) == (aud(10_00), aud(20_00), aud(30_00))
    assert (by[2].sole, by[2].joint_share, by[2].total) == (aud(20_00), aud(20_00), aud(40_00))


def test_the_tag_filter_narrows_every_figure_and_matches_case_insensitively() -> None:
    p = position(
        [
            acct(1, valuations=(val(1, D, 10_00),), tags=("Retirement",)),
            acct(2, valuations=(val(2, D, 20_00),), tags=("everyday",)),
            acct(3, "loans", valuations=(val(3, D, 5_00),), tags=("retirement",)),
            acct(4, valuations=(), tags=("everyday",)),
        ],
        tag="RETIREMENT",
    )
    assert [a.account_id for a in lines(p)] == [1, 3]
    assert p.net.household == aud(5_00)
    assert p.classes[0].figures.household == aud(10_00)
    assert p.flagged == 0  # the flagged "everyday" account is filtered out, not counted
    assert position([acct(1, tags=("x",))], tag="nope").classes[0].categories == ()


def test_empty_categories_are_omitted_and_both_classes_always_present() -> None:
    p = position([])
    assert [c.class_ for c in p.classes] == ["asset", "liability"]
    assert all(c.categories == () for c in p.classes)
    assert p.net.household == aud(0)
    assert [m.member_id for m in p.net.members] == [1, 2]


# --- Hypothesis invariants -----------------------------------------------------------------

DAY0 = date(2025, 1, 1)
days = st.integers(0, 60).map(lambda n: DAY0 + timedelta(days=n))


@st.composite
def owners_st(draw: st.DrawFn) -> tuple[tuple[tuple[int, int], ...], str]:
    n = draw(st.integers(1, 3))
    members = draw(st.permutations([1, 2, 3]))[:n]
    if n == 1:
        return ((members[0], 10000),), "sole"
    cuts = sorted(draw(st.lists(st.integers(1, 9999), min_size=n - 1, max_size=n - 1, unique=True)))
    shares = [b - a for a, b in zip([0, *cuts], [*cuts, 10000], strict=True)]
    return tuple(zip(members, shares, strict=True)), "joint"


@st.composite
def accounts_st(draw: st.DrawFn) -> list[AccountFacts]:
    out: list[AccountFacts] = []
    for id_ in range(1, draw(st.integers(0, 8)) + 1):
        ccy = draw(st.sampled_from(["AUD", "AUD", "GBP", "JPY"]))
        points = tuple(
            ValuationPoint(
                id_ * 100 + i,
                draw(days),
                draw(st.sampled_from(SOURCES)),
                Money(draw(st.integers(0, 10**12)), ccy),
            )
            for i in range(draw(st.integers(0, 3)))
        )
        shares, mode = draw(owners_st())
        out.append(
            AccountFacts(
                id_,
                draw(st.sampled_from(["B", "a", "C"])),
                draw(st.sampled_from([c.key for c in CATEGORIES])),
                ccy,
                mode,
                shares,
                tuple(sorted(draw(st.sets(st.sampled_from(["x", "y", "z"]))))),
                draw(st.none() | days),
                draw(st.none() | days),
                points,
            )
        )
    return out


fx_st = st.lists(
    st.builds(
        lambda i, ccy, day, rate: FxPoint(i, ccy, "AUD", day, Decimal(rate).scaleb(-4)),
        st.integers(1, 10**6),
        st.sampled_from(["GBP", "JPY"]),
        days,
        st.integers(1, 10**6),
    ),
    max_size=4,
    unique_by=lambda p: p.id,
)

PROP = settings(max_examples=150, deadline=None)
ALL = (1, 2, 3)


def check_additive(f: Figures) -> None:
    assert sum((m.total for m in f.members), aud(0)) == f.household
    assert sum((m.sole for m in f.members), aud(0)) + f.joint == f.household


@PROP
@given(accounts_st(), fx_st, days, st.sampled_from([None, "x", "y", "z"]))
def test_members_plus_joint_equal_household_at_every_level(
    accounts: list[AccountFacts], fx: list[FxPoint], on: date, tag: str | None
) -> None:
    p = position(accounts, on=on, fx=fx, tag=tag, members=ALL)
    check_additive(p.net)
    for c in p.classes:
        check_additive(c.figures)
        for k in c.categories:
            check_additive(k.figures)


@PROP
@given(accounts_st(), fx_st, days)
def test_household_is_the_sum_of_the_accounts_and_splits_are_exact(
    accounts: list[AccountFacts], fx: list[FxPoint], on: date
) -> None:
    p = position(accounts, on=on, fx=fx, members=ALL)
    assert p.net.household == total([a.value for a in lines(p)])
    assert p.net.joint == total([a.value for a in lines(p) if a.ownership == "joint"])
    for a in lines(p):
        assert sum((m for _, m in a.shares), aud(0)) == (a.value or aud(0))
    assert p.net.household == p.classes[0].figures.household + p.classes[1].figures.household
    assert p.flagged == sum(1 for a in lines(p) if a.value is None)
    assert p.flagged == sum(c.flagged for c in p.classes)


@PROP
@given(accounts_st(), fx_st, days, st.sampled_from(["x", "y", "z"]))
def test_the_tag_filter_keeps_exactly_the_tagged_accounts_and_their_figures(
    accounts: list[AccountFacts], fx: list[FxPoint], on: date, tag: str
) -> None:
    everything = position(accounts, on=on, fx=fx, members=ALL)
    filtered = position(accounts, on=on, fx=fx, tag=tag, members=ALL)
    tagged = {a.id for a in accounts if tag in a.tags}
    expected = [a for a in lines(everything) if a.account_id in tagged]
    assert sorted(lines(filtered), key=lambda a: a.account_id) == sorted(
        expected, key=lambda a: a.account_id
    )
    assert filtered.net.household == total([a.value for a in expected])
    assert filtered.flagged == sum(1 for a in expected if a.value is None)


@PROP
@given(accounts_st(), fx_st, days)
def test_only_accounts_open_on_the_date_are_counted(
    accounts: list[AccountFacts], fx: list[FxPoint], on: date
) -> None:
    p = position(accounts, on=on, fx=fx, members=ALL)
    assert {a.account_id for a in lines(p)} == {a.id for a in accounts if counts_on(a, on)}
