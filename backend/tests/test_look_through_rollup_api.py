"""The Household look-through roll-up through the real app on the synthetic household (integration).

The joint investment account holds, on 2026-09-30, the Broad Market ETF (33 units), the Mining share
(151), the Diversified Fund (2,000, a fund of funds) and cash: 9,951.01 in all, an odd cent, held
50/50 by Alex and Jordan, so the Member split has a cent to place. Alex's sole Super account is
analysed too (from its Super statement); everything else is Not analysed.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from finance360.capabilities.imports import commit, service
from finance360.config import Settings
from finance360.core.audit import AuditContext
from finance360.core.models.account import Account
from finance360.main import create_app
from support import TestClient, owner_client

CTX = AuditContext("owner", "owner", "api")
REPO = Path(__file__).resolve().parents[2]
IMPORTS = REPO / "fixtures" / "sample_household" / "imports"
JOINT = "Joint investment account (Sample Brokerage)"
SUPER = "Sample Super Fund"
ON = "2026-09-30"


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(REPO / "fixtures" / "sample_household" / "household.json", data / "household.json")
    settings = Settings(data_dir=data, port=8000, git_sha="dev", static_dir=None)
    with owner_client(create_app(settings)) as c:
        yield c


def _commit_joint_history(c: TestClient) -> None:
    """Commit the fixture's trades and cash for the joint Account, as the review steps do."""
    with c.app.state.session_factory.begin() as s:  # type: ignore[attr-defined]
        joint = s.scalars(select(Account).where(Account.name == JOINT)).one()
        for name in (
            "investment_transactions_accSAMPLE-PI-0002_03-Aug-2026_30-Sep-2026.csv",
            "cash_transactions_accSAMPLE-PI-0002_01-Jul-2026_30-Sep-2026.csv",
        ):
            staged = service.stage(s, CTX, (IMPORTS / name).read_bytes(), name, joint.id)
            commit.commit(s, CTX, staged.import_.id)


@pytest.fixture
def rolled(client: TestClient) -> dict[str, Any]:
    _commit_joint_history(client)
    res = client.get("/api/look-through", params={"on": ON})
    assert res.status_code == 200, res.text
    return res.json()


def _minor(figure: dict[str, Any]) -> int:
    return figure["amount_minor"]


def _lines(figures: dict[str, Any]) -> dict[tuple[str, str | None], int]:
    return {(x["kind"], x["key"]): _minor(x["amount"]) for x in figures["lines"]}


def test_golden_the_joint_account_is_split_between_its_members_to_the_cent(
    rolled: dict[str, Any],
) -> None:
    joint = next(a for a in rolled["accounts"] if a["name"] == JOINT)
    assert (joint["ownership"], _minor(joint["figures"]["total"])) == ("joint", 995101)
    alex, jordan = rolled["members"][0], rolled["members"][1]
    assert (alex["name"], jordan["name"]) == ("Alex Sample", "Jordan Sample")
    # Jordan holds no analysed Account but this one: half of 9,951.01 (the odd cent to Alex, the
    # first owner, as the Position allocates it).
    assert _minor(jordan["figures"]["analysed"]) == 497550
    mine = _lines(jordan["figures"])
    whole = _lines(joint["figures"])
    # per line, Alex's part (his Account total less Jordan's) and Jordan's sum to the Account's
    # line; Alex's own Super Account adds to his figures, never to Jordan's.
    assert set(mine) <= set(whole)
    assert all(0 <= mine[k] <= whole[k] for k in mine)
    assert sum(mine.values()) == 497550
    # Alex has the other 4,975.51 plus the Super Account, which is analysed from its statement
    super_ = next(a for a in rolled["accounts"] if a["name"] == SUPER)
    assert _minor(alex["figures"]["analysed"]) == 497551 + _minor(super_["figures"]["total"])
    ours = _lines(alex["figures"])
    sup = _lines(super_["figures"])
    for key, amount in whole.items():
        assert ours.get(key, 0) - sup.get(key, 0) == amount - mine.get(key, 0), key


def test_every_level_adds_up_to_the_position(client: TestClient, rolled: dict[str, Any]) -> None:
    position = client.get("/api/position", params={"on": ON}).json()
    net = _minor(rolled["position_net"])
    assert rolled["position_net"]["amount"] == position["net"]["household"]
    assert _minor(rolled["household"]["total"]) == net
    assert sum(_minor(m["figures"]["total"]) for m in rolled["members"]) == net
    assert sum(_minor(c["figures"]["total"]) for c in rolled["categories"]) == net
    listed = sum(_minor(n["value"]) for n in rolled["not_analysed"] if n["value"] is not None)
    assert sum(_minor(a["figures"]["total"]) for a in rolled["accounts"]) + listed == net
    # analysed + Not analysed is the total, at every level
    for figures in (
        rolled["household"],
        *(m["figures"] for m in rolled["members"]),
        *(c["figures"] for c in rolled["categories"]),
    ):
        assert _minor(figures["analysed"]) + _minor(figures["not_analysed"]) == _minor(
            figures["total"]
        )
        assert sum(_minor(x["amount"]) for x in figures["lines"]) == _minor(figures["analysed"])
    # each Member's total is the Position's figure for them
    shares = {m["member_id"]: m["total"] for m in position["net"]["members"]}
    for m in rolled["members"]:
        assert m["figures"]["total"]["amount"] == shares[m["member_id"]]


def test_accounts_with_no_look_through_are_listed_as_not_analysed_with_their_values(
    rolled: dict[str, Any],
) -> None:
    listed = {n["name"]: n for n in rolled["not_analysed"]}
    assert listed["Family Home"]["value"] is not None
    # Its kind has no look-through, which the screen says once per Category.
    assert listed["Family Home"]["reason"] is None
    assert _minor(listed["Sample Home Loan"]["value"]) < 0  # a loan counts negative, as in Position
    assert JOINT not in listed and SUPER not in listed


def test_the_default_date_is_where_the_imports_reach_and_a_dimension_can_be_chosen(
    client: TestClient,
) -> None:
    _commit_joint_history(client)
    default = client.get("/api/look-through").json()
    assert default["as_at"] == ON and default["dimension_key"] == "asset_class"
    region = client.get("/api/look-through", params={"dimension": "region"}).json()
    assert region["dimension_key"] == "region" and region["dimension_name"] == "Region"
    # a fund-of-funds resolves through its underlying funds: the joint Account has Australia
    joint = next(a for a in region["accounts"] if a["name"] == JOINT)
    assert any(x["key"] == "australia" for x in joint["figures"]["lines"])
    assert region["position_net"] == default["position_net"]


def test_an_unknown_dimension_is_a_validation_error(client: TestClient) -> None:
    res = client.get("/api/look-through", params={"dimension": "galaxies"})
    assert res.status_code == 422 and res.json()["errors"][0]["field"] == "dimension"


def test_with_no_holdings_history_the_joint_account_is_not_analysed_and_the_total_still_holds(
    client: TestClient,
) -> None:
    res = client.get("/api/look-through", params={"on": ON}).json()
    joint = next(n for n in res["not_analysed"] if n["name"] == JOINT)
    assert "no committed Import" in joint["reason"]
    assert _minor(joint["value"]) == 0  # opened empty: its one Valuation is 0.00
    assert _minor(res["household"]["total"]) == _minor(res["position_net"])
