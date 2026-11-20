"""Break-even search for a Tax what-if (ADR-0019 section 6, issue #12).

The pure search is tested with invented net-outcome functions and, for the golden case, with the
engine on invented rules (``tax_support``: fictional years, figures unlike any real rate). The
integration tests run against the synthetic household, the shipped AU pack for FY 2025-26 and a
real temporary SQLite database, through the real API. Every figure is invented.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from tax_support import aud, member, tax_input

from finance360.capabilities.tax import breakeven
from finance360.capabilities.tax.engine import estimate as run_engine
from finance360.capabilities.tax.input import TaxInput
from finance360.config import Settings
from finance360.core.models.audit import AuditEvent
from finance360.core.models.tax_estimate import TaxEstimate, TaxWhatIf
from finance360.core.money import Money
from finance360.main import create_app
from support import TestClient, owner_client

REPO = Path(__file__).resolve().parents[2]
SAMPLE = REPO / "fixtures" / "sample_household"
YEAR = 2026
ALEX, JORDAN = 1, 2
RUN = {"financial_year": YEAR, "dependants": 0, "member_ids": [ALEX, JORDAN]}


# --- the search (pure) ---------------------------------------------------------------------


def test_a_rising_outcome_gives_the_smallest_amount_that_meets_the_target() -> None:
    got = breakeven.search(lambda x: x // 3, 1_000)
    assert got.status == breakeven.FOUND and got.direction == breakeven.SMALLEST
    assert got.found_minor == 3_000  # 3000 // 3 = 1000; 2999 // 3 = 999
    assert got.at is not None and got.below is not None and got.above is not None
    assert (got.below.amount_minor, got.below.holds) == (2_999, False)
    assert (got.at.amount_minor, got.at.holds) == (3_000, True)
    assert got.above.holds
    assert got.verified and not got.non_monotone


def test_a_falling_outcome_gives_the_largest_amount_that_meets_the_target() -> None:
    got = breakeven.search(lambda x: 50_000 - x // 2, 40_000)
    assert got.direction == breakeven.LARGEST
    assert got.found_minor == 20_001  # 50000 - 10000 = 40000; 20002 // 2 = 10001 fails
    assert got.verified and not got.non_monotone


def test_no_crossing_in_range_is_reported_not_invented() -> None:
    nothing = breakeven.search(lambda x: -1, 0)
    assert nothing.status == breakeven.NONE and nothing.found_minor is None
    everywhere = breakeven.search(lambda x: 5, 0)
    assert everywhere.status == breakeven.ALWAYS and everywhere.found_minor is None
    # the target is met only beyond the upper bound
    assert breakeven.search(lambda x: x, 10_000, upper_minor=9_999).status == breakeven.NONE


def test_the_range_must_be_a_real_range() -> None:
    for lower, upper in ((-1, 10), (10, 10), (11, 10)):
        with pytest.raises(breakeven.BreakEvenError):
            breakeven.search(lambda x: x, 1, lower_minor=lower, upper_minor=upper)


def test_an_outcome_that_rises_then_falls_is_flagged_and_the_lowest_crossing_is_returned() -> None:
    def tent(x: int) -> int:
        return x if x <= 300_000_000 else 600_000_000 - x

    got = breakeven.search(tent, 200_000_000)
    assert got.found_minor == 200_000_000 and got.direction == breakeven.SMALLEST
    assert set(got.reasons) == {breakeven.MANY_CROSSINGS, breakeven.REVERSAL}
    assert got.non_monotone and got.verified  # it is a real crossing, but not the only one


def test_a_step_at_the_crossing_is_flagged_a_cliff() -> None:
    def stepped(x: int) -> int:
        return -(x // 10) - (460_000 if x >= 8_000_010 else 0)

    got = breakeven.search(stepped, stepped(8_000_000))
    assert got.found_minor == 8_000_009  # the last amount before the step
    assert got.reasons == (breakeven.CLIFF,) and got.verified


@settings(max_examples=150, deadline=None)
@given(slope=st.integers(1, 50), target=st.integers(1, 5_000_000_000))
def test_a_found_amount_always_meets_the_condition_and_is_exact(slope: int, target: int) -> None:
    got = breakeven.search(lambda x: slope * x, target)
    needed = -(-target // slope)  # the smallest x with slope * x >= target, worked independently
    if needed > breakeven.DEFAULT_UPPER_MINOR:
        assert got.status == breakeven.NONE
        return
    assert got.status == breakeven.FOUND and got.found_minor == needed
    assert slope * needed >= target > slope * (needed - 1)
    assert got.verified and not got.non_monotone


@settings(max_examples=150, deadline=None)
@given(slope=st.integers(1, 50), start=st.integers(1, 10**11), gap=st.integers(0, 10**9))
def test_a_falling_outcome_property(slope: int, start: int, gap: int) -> None:
    target = start - gap
    got = breakeven.search(lambda x: start - slope * x, target)
    largest = gap // slope
    if largest >= breakeven.DEFAULT_UPPER_MINOR:
        assert got.status == breakeven.ALWAYS
        return
    assert got.status == breakeven.FOUND and got.found_minor == largest
    assert start - slope * largest >= target
    assert got.verified


# --- the golden case: a HELP repayment cliff, through the engine ----------------------------


def _salary_outcome(base: TaxInput) -> Callable[[int], int]:
    def net_at(amount: int) -> int:
        who = base.members[0]
        kept = tuple(t for t in who.totals if t[0] != "salary")
        changed = replace(
            base, members=(replace(who, totals=(*kept, ("salary", Money(amount, "AUD")))),)
        )
        return run_engine(changed).household.net_outcome.amount_minor

    return net_at


def test_a_help_repayment_cliff_is_found_and_flagged() -> None:
    # Invented rules: a HELP debt repays 2% then 4% of income up to 80,000.00, then 7% of ALL
    # income above it, so one dollar over 80,000 costs 4,600.00 more (taxable income is whole
    # dollars, so 80,000.99 is still 80,000). The target is what 80,000.00 gives.
    base = tax_input(member(1, salary="50000", help_debt=True))
    net_at = _salary_outcome(base)
    target = net_at(aud("80000").amount_minor)
    # the repayment really does jump by more than 4,600.00
    assert net_at(8_000_000) - net_at(8_000_100) > 460_000

    got = breakeven.search(net_at, target)
    assert got.status == breakeven.FOUND and got.direction == breakeven.LARGEST
    assert got.found_minor == 8_000_099
    assert got.at is not None and got.at.net_minor >= target
    assert got.above is not None and got.above.net_minor < target
    assert got.verified
    assert got.reasons == (breakeven.CLIFF,) and got.non_monotone


def test_without_a_cliff_the_same_search_is_not_flagged() -> None:
    base = tax_input(member(1, salary="50000"))  # no HELP debt
    net_at = _salary_outcome(base)
    got = breakeven.search(net_at, net_at(6_000_000))
    assert got.status == breakeven.FOUND and got.verified and not got.non_monotone
    assert got.found_minor is not None and 6_000_000 <= got.found_minor < 6_000_100


# --- the service and API, against the synthetic household ------------------------------------


@pytest.fixture
def sample(tmp_path: Path) -> Iterator[TestClient]:
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(SAMPLE / "household.json", data / "household.json")
    app = create_app(Settings(data_dir=data, port=8000, git_sha="dev", static_dir=None))
    with owner_client(app) as c:
        yield c


def _factory(client: TestClient) -> sessionmaker[Session]:
    return client.app.state.session_factory  # type: ignore[attr-defined, no-any-return]


def _counts(client: TestClient) -> tuple[int, int, int]:
    with _factory(client)() as session:
        return (
            session.scalar(select(func.count()).select_from(TaxWhatIf)) or 0,
            session.scalar(select(func.count()).select_from(TaxEstimate)) or 0,
            session.scalar(select(func.count()).select_from(AuditEvent)) or 0,
        )


def _search(client: TestClient, **extra: Any) -> dict[str, Any]:
    body = {**RUN, "member_id": JORDAN, "label": "salary", **extra}
    res = client.post("/api/tax/whatifs/break-even", json=body)
    assert res.status_code == 200, res.text
    return dict(res.json())


def _minor(text: str) -> int:
    sign = -1 if text.startswith("-") else 1
    whole, _, cents = text.lstrip("-").partition(".")
    return sign * (int(whole) * 100 + int(cents or 0))


def _text(minor: int) -> str:
    return f"{minor // 100}.{minor % 100:02d}"


def _net(client: TestClient, overrides: list[dict[str, Any]]) -> int:
    """The household net outcome (minor units) of a one-off what-if: made, calculated, deleted."""
    made = client.post(
        "/api/tax/whatifs",
        json={"name": "probe", "financial_year": YEAR, "overrides": overrides},
    )
    assert made.status_code == 201, made.text
    res = client.post("/api/tax/estimates/preview", json={**RUN, "whatif_id": made.json()["id"]})
    assert res.status_code == 200, res.text
    client.delete(f"/api/tax/whatifs/{made.json()['id']}")
    return _minor(str(res.json()["household"]["net_outcome"]))


def test_a_crossing_is_returned_and_the_engine_agrees_one_cent_either_side(
    sample: TestClient,
) -> None:
    got = _search(sample)
    assert got["status"] == "found" and got["verified"] is True
    assert got["direction"] == "largest"  # more salary, less net outcome
    base = sample.post("/api/tax/estimates/preview", json=RUN).json()["household"]["net_outcome"]
    assert got["target"] == base
    target = _minor(base)
    cents = _minor(got["amount"])

    def stated(minor: int) -> list[dict[str, Any]]:
        return [
            {"op": "set", "member_id": JORDAN, "label": "salary", "amount": _text(minor)},
        ]

    # independently, with a real what-if each: the target is met at the amount, not one cent over
    assert _net(sample, stated(cents)) >= target
    assert _net(sample, stated(cents + 1)) < target
    assert got["at"]["amount"] == got["amount"] and got["at"]["meets_target"] is True
    assert got["above"]["meets_target"] is False and got["below"]["meets_target"] is True


def test_the_other_overrides_of_a_what_if_stay_in_force(sample: TestClient) -> None:
    records = sample.get("/api/tax/records", params={"financial_year": YEAR}).json()
    record = next(r for r in records if r["description"] == "Donation to Sample Charity")
    made = sample.post(
        "/api/tax/whatifs",
        json={
            "name": "No donation",
            "financial_year": YEAR,
            "overrides": [{"op": "exclude_record", "record_id": record["id"]}],
        },
    )
    assert made.status_code == 201, made.text
    plain = _search(sample)
    with_it = _search(sample, whatif_id=made.json()["id"])
    # leaving a donation out costs Alex, so Jordan's salary has less room before the base is missed
    assert _minor(with_it["amount"]) < _minor(plain["amount"])


def test_nothing_is_stored_by_a_search_only_by_saving(sample: TestClient) -> None:
    before = _counts(sample)
    got = _search(sample)
    assert _counts(sample) == before  # no what-if, no estimate, no audit entry
    saved = sample.post(
        "/api/tax/whatifs",
        json={
            "name": "Jordan at break-even",
            "financial_year": YEAR,
            "overrides": [
                {"op": "set", "member_id": JORDAN, "label": "salary", "amount": got["amount"]}
            ],
        },
    )
    assert saved.status_code == 201
    assert _counts(sample)[0] == before[0] + 1
    assert saved.json()["overrides"][0]["value"]["amount"] == got["amount"]


def test_a_search_refuses_what_it_cannot_search(sample: TestClient) -> None:
    # Days and flags have no amount to search; an unknown label is refused with the shared shape.
    for label in ("surcharge_exempt_days", "help_debt", "no_such_label"):
        res = sample.post(
            "/api/tax/whatifs/break-even", json={**RUN, "member_id": ALEX, "label": label}
        )
        assert res.status_code == 422
        assert res.json()["errors"][0]["field"] == "label"
    res = sample.post(
        "/api/tax/whatifs/break-even", json={**RUN, "member_id": 99, "label": "salary"}
    )
    assert res.status_code == 422 and res.json()["errors"][0]["field"] == "member_id"
