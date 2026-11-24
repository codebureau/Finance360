"""Super Investment options in the look-through (ADR-0030 sections 4 and 6; #86), through the real
app on the synthetic household (integration).

``Sample Super Fund`` has a Super statement at 2026-06-30 with three options (Growth 68,250.00,
Balanced 90,000.00, High Growth 53,250.01: 211,500.01). The fixture maps all three to fund
Instruments, gives the first two a profile and the last none. The Account's Valuation of
215,228.07 on 2026-09-30 is dated after the statement, so Unallocated is the "change since" it:
3,728.06.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from finance360.config import Settings
from finance360.core.models.audit import AuditEvent
from finance360.core.models.exposure import InvestmentOptionLink
from finance360.main import create_app
from support import TestClient, owner_client

REPO = Path(__file__).resolve().parents[2]
FUND = "Sample Super Fund"
ON = "2026-09-30"


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(REPO / "fixtures" / "sample_household" / "household.json", data / "household.json")
    settings = Settings(data_dir=data, port=8000, git_sha="dev", static_dir=None)
    with owner_client(create_app(settings)) as c:
        yield c


def _account(c: TestClient, name: str = FUND) -> int:
    return next(a["id"] for a in c.get("/api/accounts").json() if a["name"] == name)


def _view(c: TestClient, account: int, **params: str) -> dict[str, Any]:
    res = c.get(f"/api/accounts/{account}/look-through", params=params)
    assert res.status_code == 200, res.text
    return res.json()


def _rows(view: dict[str, Any]) -> list[tuple[str, str, str]]:
    return [(x["kind"], x["name"], x["amount"]["amount"]) for x in view["lines"]]


def _total_minor(view: dict[str, Any]) -> int:
    return sum(x["amount"]["amount_minor"] for x in view["lines"])


def _instrument(c: TestClient, name: str) -> int:
    return next(i["id"] for i in c.get("/api/instruments").json() if i["name"] == name)


def _map(c: TestClient, account: int, name: str, **body: Any) -> Any:
    return c.put(
        f"/api/accounts/{account}/investment-option-links", json={"option_name": name, **body}
    )


def _unmap_growth(c: TestClient) -> None:
    """Drop the fixture's first mapping (Growth), as if the owner had never made it."""
    with c.app.state.session_factory.begin() as s:  # type: ignore[attr-defined]
        s.delete(s.scalars(select(InvestmentOptionLink).order_by(InvestmentOptionLink.id)).first())


def test_golden_the_super_account_shows_its_options_the_gap_and_what_has_no_profile(
    client: TestClient,
) -> None:
    fund = _account(client)
    view = _view(client, fund, on=ON)
    assert (view["applies"], view["status"], view["source"]) == (True, "ok", "super_statement")
    assert (view["statement_date"], view["change_since"]) == ("2026-06-30", "2026-06-30")
    assert view["total"]["amount"] == "215228.07"
    # Growth 68,250.00 x (38, 40, 7, 4, 6, 3, 2)% and Balanced 90,000.00 x (25, 27, 7, 4, 24, 10,
    # 3)%, worked by hand; High Growth (53,250.01) has no profile; Unallocated is the rest.
    assert _rows(view) == [
        ("label", "Australian shares", "48435.00"),  # 25,935.00 + 22,500.00
        ("label", "International shares", "51600.00"),  # 27,300.00 + 24,300.00
        ("label", "Property", "11077.50"),  # 4,777.50 + 6,300.00
        ("label", "Infrastructure", "6330.00"),  # 2,730.00 + 3,600.00
        ("label", "Fixed income", "25695.00"),  # 4,095.00 + 21,600.00
        ("label", "Cash", "11047.50"),  # 2,047.50 + 9,000.00
        ("label", "Alternatives", "4065.00"),  # 1,365.00 + 2,700.00
        ("no_profile", "No profile: Sample Super High Growth Option", "53250.01"),
        ("unallocated", "Unallocated", "3728.06"),
    ]
    assert _total_minor(view) == 21522807
    # 1.73% of the Account: beyond the 1% reconcile tolerance but well within the 15% stale one
    assert view["flagged"] is False
    assert view["tolerance"]["amount"] == "32284.21"
    no_profile = next(x for x in view["lines"] if x["kind"] == "no_profile")
    assert no_profile["instrument_id"] == _instrument(client, "Sample Super High Growth Option")
    growth = next(p for p in view["parts"] if p["option_name"] == "Growth")
    assert (growth["instrument_name"], growth["amount"]["amount"]) == (
        "Sample Super Growth Option",
        "68250.00",
    )


def test_the_option_values_are_never_scaled_to_the_account_value(client: TestClient) -> None:
    view = _view(client, _account(client), on=ON)
    assert [p["amount"]["amount"] for p in view["parts"]] == ["68250.00", "90000.00", "53250.01"]


def test_on_the_statement_date_the_gap_is_judged_against_the_one_percent_tolerance(
    client: TestClient,
) -> None:
    """On the statement date the Valuation is the statement's own 211,500.00: Unallocated is
    -0.01 (the options total a cent more), shown quietly and with no "change since"."""
    view = _view(client, _account(client), on="2026-06-30")
    assert view["change_since"] is None and view["flagged"] is False
    unallocated = next(x for x in view["lines"] if x["kind"] == "unallocated")
    assert unallocated["amount"]["amount"] == "-0.01"
    assert view["tolerance"]["amount"] == "2115.00"
    assert _total_minor(view) == 21150000


def test_a_stale_mix_is_flagged_only_beyond_fifteen_percent(client: TestClient) -> None:
    fund = _account(client)

    def valuation(as_at: str, amount: str) -> None:
        res = client.post(
            f"/api/accounts/{fund}/valuations",
            json={"as_at": as_at, "amount": amount, "provenance_note": "Invented"},
        )
        assert res.status_code == 201, res.text

    valuation("2026-10-31", "248000.00")  # a 36,499.99 gap: 14.7%, within 15% (37,200.00)
    quiet = _view(client, fund, on="2026-10-31")
    assert (quiet["flagged"], quiet["change_since"]) == (False, "2026-06-30")
    valuation("2026-11-15", "260000.00")  # a 48,499.99 gap: beyond 15% of 260,000.00
    flagged = _view(client, fund, on="2026-11-15")
    assert flagged["flagged"] is True
    assert flagged["tolerance"]["amount"] == "39000.00"
    assert _total_minor(flagged) == 26000000


def test_an_option_mix_above_the_account_total_shows_a_negative_unallocated_line(
    client: TestClient,
) -> None:
    fund = _account(client)
    res = client.post(
        f"/api/accounts/{fund}/valuations",
        json={"as_at": "2026-07-15", "amount": "200000.00", "provenance_note": "Invented"},
    )
    assert res.status_code == 201, res.text
    view = _view(client, fund, on="2026-07-15")
    unallocated = next(x for x in view["lines"] if x["kind"] == "unallocated")
    assert unallocated["amount"]["amount"] == "-11500.01"  # the parts keep their stated values
    assert view["flagged"] is False  # within the stale 15% of 200,000.00
    assert _total_minor(view) == 20000000


def test_with_no_super_statement_the_account_is_not_analysed_with_a_prompt(
    client: TestClient,
) -> None:
    view = _view(client, _account(client), on="2026-05-01")
    assert (view["applies"], view["status"], view["source"]) == (
        True,
        "not_analysed",
        "super_statement",
    )
    assert view["total"] is None and view["lines"] == []
    assert "Provide a Super statement" in view["reason"]


def test_an_account_that_is_neither_holdings_nor_super_has_no_look_through(
    client: TestClient,
) -> None:
    view = _view(client, _account(client, "Family Home"))
    assert (view["applies"], view["source"]) == (False, None)


def test_an_unmapped_option_is_a_no_profile_line_naming_the_option(client: TestClient) -> None:
    fund = _account(client)
    _unmap_growth(client)
    view = _view(client, fund, on=ON)
    line = next(x for x in view["lines"] if x["option_name"] == "Growth")
    assert (line["kind"], line["instrument_id"], line["amount"]["amount"]) == (
        "no_profile",
        None,
        "68250.00",
    )
    assert line["name"] == "No profile: Growth (not mapped to a fund)"
    assert _total_minor(view) == 21522807


def test_mapping_an_option_by_creating_a_fund_from_its_name(client: TestClient) -> None:
    fund = _account(client)
    _unmap_growth(client)
    res = _map(client, fund, "Growth", create_instrument=True)
    assert res.status_code == 200, res.text
    made = next(
        i for i in client.get("/api/instruments").json() if i["id"] == res.json()["instrument_id"]
    )
    assert (made["name"], made["kind"], made["currency"]) == (
        "Growth (Sample Super Fund)",
        "fund",
        "AUD",
    )
    # the new Instrument has no profile, so the option reads No profile naming it, linked
    lines = _view(client, fund, on=ON)["lines"]
    line = next(x for x in lines if x["name"] == "No profile: Growth (Sample Super Fund)")
    assert line["instrument_id"] == made["id"] and line["option_name"] is None


def test_remapping_is_edited_in_place_and_audited_and_names_fold_case_and_spacing(
    client: TestClient,
) -> None:
    fund = _account(client)
    balanced = _instrument(client, "Sample Super Balanced Option")
    res = _map(client, fund, "  growth ", instrument_id=balanced)  # another spelling of Growth
    assert res.status_code == 200, res.text
    assert res.json() == {
        "option_name": "growth",
        "instrument_id": balanced,
        "instrument_name": "Sample Super Balanced Option",
    }
    with client.app.state.session_factory() as s:  # type: ignore[attr-defined]
        assert len(s.scalars(select(InvestmentOptionLink)).all()) == 3  # re-pointed, not added
        trail = s.scalars(
            select(AuditEvent).where(AuditEvent.entity_type == "investment_option_link")
        ).all()
        assert sorted(e.action for e in trail) == ["create", "create", "create", "update"]
        update = next(e for e in trail if e.action == "update")
        assert update.before["instrument_name"] == "Sample Super Growth Option"  # type: ignore[index]
        assert update.after["instrument_name"] == "Sample Super Balanced Option"  # type: ignore[index]
    # Growth now reads Balanced's profile too: 10% cash of (68,250.00 + 90,000.00)
    cash = next(x for x in _view(client, fund, on=ON)["lines"] if x["key"] == "cash")
    assert cash["amount"]["amount"] == "15825.00"
    # the Account's audit trail lists the mapping
    events = client.get("/api/audit", params={"account_id": fund}).json()["items"]
    assert any(e["entity_type"] == "investment_option_link" for e in events)


def test_mapping_is_validated_with_the_shared_error_shape(client: TestClient) -> None:
    fund = _account(client)
    balanced = _instrument(client, "Sample Super Balanced Option")
    for body in (
        {"option_name": "  ", "instrument_id": balanced},
        {"option_name": "Growth"},
        {"option_name": "Growth", "instrument_id": balanced, "create_instrument": True},
    ):
        res = client.put(f"/api/accounts/{fund}/investment-option-links", json=body)
        assert res.status_code == 422, body
        assert res.json()["errors"][0]["field"] in {"option_name", "instrument_id"}
    assert _map(client, fund, "Growth", instrument_id=99999).status_code == 404
    other = _account(client, "Sample Brokerage account")
    res = _map(client, other, "Growth", instrument_id=balanced)
    assert res.status_code == 422 and res.json()["errors"][0]["field"] == "account_id"
    assert _map(client, 99999, "Growth", instrument_id=balanced).status_code == 404


def test_a_fund_of_funds_works_through_a_mapped_option(client: TestClient) -> None:
    """Map Growth to the fixture's fund-of-funds: it resolves through its underlying ETFs."""
    fund = _account(client)
    diversified = _instrument(client, "Sample Diversified Fund")
    assert _map(client, fund, "Growth", instrument_id=diversified).status_code == 200
    view = _view(client, fund, on=ON)
    growth = next(p for p in view["parts"] if p["option_name"] == "Growth")
    assert [u["path"] for u in growth["underlying"]] == [
        ["Sample Diversified Fund", "Sample Broad Market ETF"],
        ["Sample Diversified Fund", "Sample Global Equity ETF"],
    ]
    assert any(x["through_fund"] for x in view["lines"])
    assert _total_minor(view) == 21522807
