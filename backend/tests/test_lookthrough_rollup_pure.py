"""The pure look-through roll-up (ADR-0030 section 8): golden figures worked by hand, and Hypothesis
invariants. A joint Account is apportioned so both margins hold exactly (per line the Members' parts
sum to the Account's; per Member the parts sum to their share), nothing is negative except
Unallocated, and every level adds up to the Position: Household = sum of Members = sum of
Categories = sum of Accounts plus Not analysed.
"""

from __future__ import annotations

from collections.abc import Sequence

from hypothesis import given, settings
from hypothesis import strategies as st

from finance360.capabilities.lookthrough.apportion import (
    AccountIn,
    LineIn,
    Rolled,
    apportion,
    roll_up,
)
from finance360.capabilities.lookthrough.lines import LineKind
from finance360.core.money import Money, allocate

AUD = "AUD"
LABELS = [("australian_shares", "Australian shares"), ("cash", "Cash"), ("other", "Other")]
MEMBERS = [(1, "Alex"), (2, "Jordan"), (3, "Sam")]
CATEGORIES = [("investments", "Investments"), ("cash", "Cash"), ("loans_credit", "Loans")]


def aud(minor: int) -> Money:
    return Money(minor, AUD)


def minors(parts: Sequence[Money]) -> list[int]:
    return [p.amount_minor for p in parts]


# --- golden, by hand ----------------------------------------------------------------------


def test_golden_a_joint_account_is_apportioned_by_largest_remainder_and_both_margins_hold() -> None:
    # 10.01 held 5.01 / 5.00. Alex's 501 across 600 / 301 / 100 (weights sum 1001) is
    # 300.30 / 150.65 / 50.05: floors 300 / 150 / 50, and the one leftover goes to the largest
    # remainder (the .65). Jordan, the last, takes what is left.
    split = apportion([aud(600), aud(301), aud(100)], [(1, aud(501)), (2, aud(500))])
    assert minors(split[1]) == [300, 151, 50]
    assert minors(split[2]) == [300, 150, 50]
    assert [a + b for a, b in zip(minors(split[1]), minors(split[2]), strict=True)] == [
        600,
        301,
        100,
    ]


def test_golden_a_negative_unallocated_is_allocated_by_share_first() -> None:
    # Parts 12.00 on an Account worth 10.01: Unallocated is -1.99. By share (501 : 500) that is
    # -1.00 / -0.99 (the leftover cent to the larger remainder), so Alex has 501 + 100 = 601 left to
    # place and Jordan 500 + 99 = 599: together the 1,200 of parts.
    split = apportion([aud(1200), aud(-199)], [(1, aud(501)), (2, aud(500))])
    assert minors(split[1]) == [601, -100]
    assert minors(split[2]) == [599, -99]


def test_golden_a_sole_account_goes_wholly_to_its_owner() -> None:
    assert apportion([aud(70), aud(30)], [(2, aud(100))]) == {2: (aud(70), aud(30))}


def test_golden_the_rollup_reconciles_with_not_analysed_in_every_level() -> None:
    joint = AccountIn(
        10,
        "Joint investments",
        "investments",
        "joint",
        aud(1001),
        ((1, aud(501)), (2, aud(500))),
        (
            LineIn("label", "australian_shares", aud(600)),
            LineIn("label", "cash", aud(301)),
            LineIn("unallocated", None, aud(100)),
        ),
    )
    sole = AccountIn(
        11,
        "Alex's shares",
        "investments",
        "sole",
        aud(400),
        ((1, aud(400)),),
        (
            LineIn("label", "australian_shares", aud(250)),
            LineIn("no_profile", None, aud(100)),
            LineIn("no_profile", None, aud(50)),
        ),
    )
    savings = AccountIn(
        12, "Savings", "cash", "sole", aud(900), ((2, aud(900)),), None, "Not built from Holdings."
    )
    loan = AccountIn(
        13, "Loan", "loans_credit", "sole", aud(-300), ((1, aud(-300)),), None, "Not built."
    )
    left_out = AccountIn(14, "Unvalued", "cash", "sole", None, (), None, "No value.")
    out = roll_up(
        [joint, sole, savings, loan, left_out],
        labels=LABELS,
        members=MEMBERS,
        categories=CATEGORIES,
        currency=AUD,
    )

    def lines(r: Rolled) -> list[tuple[str, int]]:
        return [(line.name, line.amount.amount_minor) for line in r.lines]

    # Household: analysed 1,401, Not analysed 900 - 300 = 600: the Position's 2,001.
    assert lines(out.household) == [
        ("Australian shares", 850),
        ("Cash", 301),
        ("No profile", 150),  # two No profile lines are one
        ("Unallocated", 100),
    ]
    assert (out.household.analysed, out.household.not_analysed, out.household.total) == (
        aud(1401),
        aud(600),
        aud(2001),
    )
    alex, jordan, sam = (m.rolled for m in out.members)
    assert (alex.total, jordan.total, sam.total) == (aud(501 + 400 - 300), aud(500 + 900), aud(0))
    assert lines(alex) == [
        ("Australian shares", 300 + 250),
        ("Cash", 151),
        ("No profile", 150),
        ("Unallocated", 50),
    ]
    assert alex.not_analysed == aud(-300) and jordan.not_analysed == aud(900)
    by_category = {c.key: c.rolled for c in out.categories}
    assert by_category["investments"].total == aud(1401)
    assert (by_category["cash"].analysed, by_category["cash"].total) == (aud(0), aud(900))
    assert by_category["loans_credit"].total == aud(-300)
    assert [(a.account_id, a.rolled.total) for a in out.accounts] == [
        (10, aud(1001)),
        (11, aud(400)),
    ]
    # The unvalued Account is listed, counted nowhere.
    assert [(n.account_id, n.value) for n in out.not_analysed] == [
        (12, aud(900)),
        (13, aud(-300)),
        (14, None),
    ]
    assert out.household.lines[0].percent == "60.67"  # 850 of the 1,401 analysed


# --- Hypothesis ---------------------------------------------------------------------------


@st.composite
def _apportioning(draw: st.DrawFn) -> tuple[list[Money], list[tuple[int, Money]], set[int]]:
    """Line amounts (not negative, bar an optional negative last line) summing to an Account value,
    and the owners' shares of it, allocated from basis points as the Position does."""
    parts = draw(st.lists(st.integers(0, 10**9), min_size=1, max_size=6))
    negative = draw(st.booleans())
    value = sum(parts)
    amounts = list(parts)
    allowed: set[int] = set()
    if negative:
        gap = draw(st.integers(0, value))
        amounts.append(-gap)
        allowed.add(len(amounts) - 1)
        value -= gap
    owners = draw(st.lists(st.integers(1, 3), min_size=1, max_size=3, unique=True))
    weights = {o: draw(st.integers(1, 9999)) for o in owners}
    shares = allocate(aud(value), weights)
    return [aud(a) for a in amounts], list(shares.items()), allowed


@given(_apportioning())
@settings(max_examples=400, deadline=None)
def test_property_apportioning_holds_both_margins_exactly_and_nothing_is_negative_but_unallocated(
    case: tuple[list[Money], list[tuple[int, Money]], set[int]],
) -> None:
    amounts, shares, allowed = case
    split = apportion(amounts, shares)
    assert set(split) == {o for o, _ in shares}
    # per line, the owners' parts sum to the Account's
    for j, amount in enumerate(amounts):
        assert sum(split[o][j].amount_minor for o in split) == amount.amount_minor
    # per owner, the parts sum to their share of the Account's value
    for owner, share in shares:
        assert sum(minors(split[owner])) == share.amount_minor
    for parts in split.values():
        for j, part in enumerate(parts):
            assert part.amount_minor >= 0 or j in allowed


@st.composite
def _accounts(draw: st.DrawFn) -> list[AccountIn]:
    out: list[AccountIn] = []
    for n in range(draw(st.integers(0, 6))):
        category = draw(st.sampled_from([k for k, _ in CATEGORIES]))
        owners = draw(st.lists(st.sampled_from([1, 2, 3]), min_size=1, max_size=3, unique=True))
        analysed = draw(st.booleans()) and category != "loans_credit"
        if analysed:
            kinds: list[tuple[LineKind, str | None]] = draw(
                st.lists(
                    st.sampled_from(
                        [
                            ("label", "australian_shares"),
                            ("label", "cash"),
                            ("label", "other"),
                            ("no_profile", None),
                            ("not_classified", None),
                        ]
                    ),
                    min_size=0,
                    max_size=6,
                )
            )
            lines = [LineIn(k, key, aud(draw(st.integers(0, 10**9)))) for k, key in kinds]
            gap = draw(st.integers(-sum(x.amount.amount_minor for x in lines), 10**6))
            lines.append(LineIn("unallocated", None, aud(gap)))
            value = sum(x.amount.amount_minor for x in lines)
        else:
            lines = None
            value = draw(st.integers(-(10**9), 10**9)) * (-1 if category == "loans_credit" else 1)
        has_value = draw(st.integers(0, 9)) > 0
        weights = {o: draw(st.integers(1, 9999)) for o in owners}
        shares = tuple(allocate(aud(value), weights).items()) if has_value else ()
        out.append(
            AccountIn(
                n,
                f"Account {n}",
                category,
                "joint" if len(owners) > 1 else "sole",
                aud(value) if has_value else None,
                shares,
                tuple(lines) if lines is not None and has_value else None,
                "reason",
            )
        )
    return out


@given(_accounts())
@settings(max_examples=300, deadline=None)
def test_property_every_level_adds_up_to_the_position(accounts: list[AccountIn]) -> None:
    out = roll_up(accounts, labels=LABELS, members=MEMBERS, categories=CATEGORIES, currency=AUD)
    counted = [a for a in accounts if a.value is not None]
    position = sum(a.value.amount_minor for a in counted if a.value is not None)
    household = out.household
    assert household.total.amount_minor == position
    assert sum(m.rolled.total.amount_minor for m in out.members) == position
    assert sum(c.rolled.total.amount_minor for c in out.categories) == position
    assert (
        sum(a.rolled.total.amount_minor for a in out.accounts)
        + sum(n.value.amount_minor for n in out.not_analysed if n.value is not None)
        == position
    )
    # each Member's total is the Position's Member share of every counted Account
    for member_id, _ in MEMBERS:
        share = sum(s.amount_minor for a in counted for m, s in a.shares if m == member_id)
        member = next(m for m in out.members if m.member_id == member_id)
        assert member.rolled.total.amount_minor == share

    # each line adds up across Members and across Categories to the Household's line
    def by_line(rolled: Rolled) -> dict[tuple[str, str | None], int]:
        return {(x.kind, x.key): x.amount.amount_minor for x in rolled.lines}

    for identity, amount in by_line(household).items():
        assert sum(by_line(m.rolled).get(identity, 0) for m in out.members) == amount
        assert sum(by_line(c.rolled).get(identity, 0) for c in out.categories) == amount
        assert sum(by_line(a.rolled).get(identity, 0) for a in out.accounts) == amount
    # nothing is negative but Unallocated
    for rolled in [household, *(m.rolled for m in out.members), *(a.rolled for a in out.accounts)]:
        for line in rolled.lines:
            assert line.amount.amount_minor >= 0 or line.kind == "unallocated"
