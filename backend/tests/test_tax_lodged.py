"""Lodged results and the return worksheet (ADR-0019 section 8, issue #13).

Integration tests against a real migrated temporary SQLite database and, for the API, the synthetic
household. Supersession is invariant work: an amended notice supersedes the earlier one, which stays
retrievable, and the right row is current. Every figure is invented.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from finance360.capabilities.household import service as household
from finance360.capabilities.library import registry
from finance360.capabilities.tax import link_kind, lodged
from finance360.capabilities.tax.lodged import LodgedInput, OtherInput
from finance360.config import Settings
from finance360.core.audit import AuditContext
from finance360.core.errors import NotFoundError, ValidationError
from finance360.core.models.audit import AuditEvent
from finance360.core.models.tax_lodged_result import TaxLodgedResult
from finance360.main import create_app
from support import TestClient, owner_client

CTX = AuditContext("owner", "owner", "api")
TODAY = date(2026, 10, 17)
REPO = Path(__file__).resolve().parents[2]
FY = 2026


def members(s: Session) -> tuple[int, int]:
    household.ensure_household(s)
    alex = household.add_member(s, CTX, "Alex Sample", date(1984, 3, 12), today=TODAY)
    jordan = household.add_member(s, CTX, "Jordan Sample", date(1986, 5, 2), today=TODAY)
    return alex.id, jordan.id


def notice(member_id: int, assessed: str = "2026-08-14", **items: str) -> LodgedInput:
    return LodgedInput(
        member_id,
        FY,
        date.fromisoformat(assessed),
        items or {"taxable_income": "50000.00", "net_outcome": "-120.50"},
    )


@pytest.fixture
def s(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    with session_factory.begin() as session:
        yield session


# --- supersession (invariant) ----------------------------------------------------------------


def test_an_amended_notice_supersedes_the_earlier_which_stays_retrievable(s: Session) -> None:
    alex, _ = members(s)
    first = lodged.create_lodged_result(s, CTX, notice(alex, taxable_income="50000.00"))
    second = lodged.amend_lodged_result(
        s, CTX, first.id, notice(alex, "2026-10-09", taxable_income="51000.00")
    )

    # the right row is current, and it is the new one
    current = lodged.current_lodged_result(s, alex, FY)
    assert current is not None and current.id == second.id
    assert dict(current.result.items)["taxable_income"].amount_minor == 5_100_000
    assert current.assessed_on == date(2026, 10, 9)

    # the earlier row is kept with what was entered, set aside and pointing at its replacement
    earlier = lodged.get_lodged_result(s, first.id)
    assert earlier.current is False
    assert earlier.superseded_by_id == second.id and earlier.superseded_at is not None
    assert dict(earlier.result.items)["taxable_income"].amount_minor == 5_000_000
    assert earlier.assessed_on == date(2026, 8, 14)

    # history: newest first, the current one then those it replaced
    assert [(v.id, v.current) for v in lodged.history(s, alex, FY)] == [
        (second.id, True),
        (first.id, False),
    ]
    assert s.scalar(select(func.count()).select_from(TaxLodgedResult)) == 2


def test_a_second_amendment_keeps_every_earlier_notice(s: Session) -> None:
    alex, _ = members(s)
    a = lodged.create_lodged_result(s, CTX, notice(alex, taxable_income="1.00"))
    b = lodged.amend_lodged_result(s, CTX, a.id, notice(alex, "2026-09-01", taxable_income="2.00"))
    c = lodged.amend_lodged_result(s, CTX, b.id, notice(alex, "2026-10-01", taxable_income="3.00"))
    rows = lodged.history(s, alex, FY)
    assert [v.id for v in rows] == [c.id, b.id, a.id]
    assert [v.current for v in rows] == [True, False, False]
    assert [v.superseded_by_id for v in rows] == [None, c.id, b.id]
    assert [dict(v.result.items)["taxable_income"].amount_minor for v in rows] == [300, 200, 100]


def test_only_the_current_notice_can_be_amended_and_only_for_its_member_and_year(
    s: Session,
) -> None:
    alex, jordan = members(s)
    first = lodged.create_lodged_result(s, CTX, notice(alex))
    lodged.amend_lodged_result(s, CTX, first.id, notice(alex, "2026-09-01"))
    with pytest.raises(ValidationError) as old:
        lodged.amend_lodged_result(s, CTX, first.id, notice(alex, "2026-09-02"))
    assert [e.field for e in old.value.errors] == ["id"]
    current = lodged.current_lodged_result(s, alex, FY)
    assert current is not None
    with pytest.raises(ValidationError) as other_member:
        lodged.amend_lodged_result(s, CTX, current.id, notice(jordan))
    assert [e.field for e in other_member.value.errors] == ["financial_year"]
    with pytest.raises(ValidationError):
        lodged.amend_lodged_result(
            s, CTX, current.id, LodgedInput(alex, FY + 1, date(2026, 9, 1), {"credits": "1.00"})
        )
    with pytest.raises(NotFoundError):
        lodged.amend_lodged_result(s, CTX, 999, notice(alex))
    # nothing changed
    assert [v.id for v in lodged.history(s, alex, FY) if v.current] == [current.id]


def test_a_member_and_year_with_a_notice_must_be_amended_not_entered_again(s: Session) -> None:
    alex, jordan = members(s)
    lodged.create_lodged_result(s, CTX, notice(alex))
    with pytest.raises(ValidationError) as error:
        lodged.create_lodged_result(s, CTX, notice(alex))
    assert error.value.errors[0].field == "financial_year"
    assert "amended notice" in error.value.errors[0].message
    lodged.create_lodged_result(s, CTX, notice(jordan))  # another Member is its own notice
    lodged.create_lodged_result(
        s, CTX, LodgedInput(alex, FY - 1, date(2025, 9, 1), {"credits": "1.00"})
    )  # and another year


def test_the_database_keeps_one_current_row_and_refuses_rewrites_and_deletes(
    s: Session,
) -> None:
    alex, _ = members(s)
    first = lodged.create_lodged_result(s, CTX, notice(alex))
    s.flush()
    for statement in (
        "UPDATE tax_lodged_result SET items_json = '{}'",
        "UPDATE tax_lodged_result SET financial_year = 2025",
        "DELETE FROM tax_lodged_result",
        # superseding is allowed once; clearing or re-pointing it is not
    ):
        with s.begin_nested(), pytest.raises(DBAPIError, match="append-only"):
            s.execute(text(statement))
    with s.begin_nested(), pytest.raises(IntegrityError):  # a second current row for the same year
        s.add(
            TaxLodgedResult(
                member_id=alex,
                financial_year=FY,
                assessed_on=date(2026, 9, 1),
                items_json="{}",
                other_json="[]",
                recorded_at=datetime(2026, 9, 1),
            )
        )
        s.flush()
    assert lodged.get_lodged_result(s, first.id).current


# --- storage and validation ------------------------------------------------------------------


def test_money_is_stored_as_integer_minor_units_in_valid_json_never_a_float(s: Session) -> None:
    alex, _ = members(s)
    view = lodged.create_lodged_result(
        s,
        CTX,
        LodgedInput(
            alex,
            FY,
            date(2026, 8, 14),
            {"net_outcome": "-1234.56", "credits": "0.07", "offsets": ""},
            [OtherInput("Sample adjustment", "10.10")],
        ),
    )
    row = s.get(TaxLodgedResult, view.id)
    assert row is not None
    assert json.loads(row.items_json) == {
        "credits": {"ccy": "AUD", "minor": 7},
        "net_outcome": {"ccy": "AUD", "minor": -123456},
    }  # a blank amount is not on the notice, so it is not stored (not zero)
    assert json.loads(row.other_json) == [
        {"amount": {"ccy": "AUD", "minor": 1010}, "name": "Sample adjustment"}
    ]
    assert "." not in row.items_json + row.other_json
    assert [(k, a.amount_minor) for k, a in view.result.items] == [
        ("credits", 7),
        ("net_outcome", -123456),
    ]
    with pytest.raises(IntegrityError), s.begin_nested():  # json_valid check
        s.execute(
            text(
                "INSERT INTO tax_lodged_result (member_id, financial_year, assessed_on, "
                "items_json, other_json, recorded_at) "
                "VALUES (1, 2025, '2025-09-01', 'not json', '[]', '2025-09-01')"
            )
        )


@pytest.mark.parametrize(
    ("given", "field"),
    [
        (LodgedInput(1, FY, None, {"credits": "1.00"}), "assessed_on"),
        (LodgedInput(1, FY, date(2026, 9, 1), {}), "items"),
        (LodgedInput(1, FY, date(2026, 9, 1), {"credits": "12.345"}), "items.credits"),
        (LodgedInput(1, FY, date(2026, 9, 1), {"credits": "abc"}), "items.credits"),
        (LodgedInput(1, FY, date(2026, 9, 1), {"bonus": "1.00"}), "items.bonus"),
        (LodgedInput(1, 1800, date(2026, 9, 1), {"credits": "1.00"}), "financial_year"),
        (LodgedInput(99, FY, date(2026, 9, 1), {"credits": "1.00"}), "member_id"),
        (
            LodgedInput(1, FY, date(2026, 9, 1), {}, [OtherInput(" ", "1.00")]),
            "other.0.name",
        ),
        (
            LodgedInput(1, FY, date(2026, 9, 1), {}, [OtherInput("X", "1.001")]),
            "other.0.amount",
        ),
        (
            LodgedInput(1, FY, date(2026, 9, 1), {}, [OtherInput("X", "1"), OtherInput("x", "2")]),
            "other.1.name",
        ),
    ],
)
def test_bad_input_is_refused_naming_the_field_and_nothing_is_written(
    s: Session, given: LodgedInput, field: str
) -> None:
    members(s)
    with pytest.raises(ValidationError) as error:
        lodged.create_lodged_result(s, CTX, given)
    assert field in [e.field for e in error.value.errors]
    assert s.scalar(select(func.count()).select_from(TaxLodgedResult)) == 0
    assert (
        s.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.entity_type == "tax_lodged_result")
        )
        == 0
    )


def test_every_write_is_audited_with_the_snapshots(s: Session) -> None:
    alex, _ = members(s)
    first = lodged.create_lodged_result(s, CTX, notice(alex, taxable_income="1.00"))
    second = lodged.amend_lodged_result(
        s, CTX, first.id, notice(alex, "2026-10-09", taxable_income="2.00")
    )
    events = list(
        s.scalars(
            select(AuditEvent)
            .where(AuditEvent.entity_type == "tax_lodged_result")
            .order_by(AuditEvent.id)
        )
    )
    assert [(e.action, e.entity_id) for e in events] == [
        ("create", first.id),
        ("supersede", second.id),
    ]
    created, superseded = events
    assert created.before is None and created.after is not None
    assert superseded.before is not None and superseded.after is not None
    assert created.after["items"]["taxable_income"] == {"ccy": "AUD", "minor": 100}
    assert superseded.before["id"] == first.id
    assert superseded.before["items"]["taxable_income"] == {"ccy": "AUD", "minor": 100}
    assert superseded.after["items"]["taxable_income"] == {"ccy": "AUD", "minor": 200}
    assert "Alex Sample" in events[0].summary and "FY 2025-26" in events[0].summary


def test_a_member_with_a_lodged_result_cannot_be_deleted(s: Session) -> None:
    alex, jordan = members(s)
    lodged.create_lodged_result(s, CTX, notice(alex))
    assert lodged.member_delete_guard(s, alex) is not None
    assert lodged.member_delete_guard(s, jordan) is None


# --- documents: the Link kind ----------------------------------------------------------------


def test_the_notices_documents_follow_an_amendment(s: Session) -> None:
    alex, jordan = members(s)
    first = lodged.create_lodged_result(s, CTX, notice(alex))
    other = lodged.create_lodged_result(s, CTX, notice(jordan))
    second = lodged.amend_lodged_result(s, CTX, first.id, notice(alex, "2026-10-09"))
    kind = link_kind.LODGED_KIND
    assert kind.exists(s, first.id) and not kind.exists(s, 999)
    # an amended notice keeps the earlier notice's documents; another Member's is separate
    assert set(kind.lineage(s, second.id)) == {first.id, second.id}
    assert set(kind.lineage(s, first.id)) == {first.id, second.id}
    assert set(kind.lineage(s, other.id)) == {other.id}
    described = kind.describe(s, [first.id, 999])
    assert described[first.id].label == "Lodged result, Alex Sample, FY 2025-26"
    assert described[first.id].route == f"/tax/lodged?member={alex}&fy={FY}"
    assert described[999].removed is True
    assert [label for _, label in kind.candidates(s, "jord")] == [
        "Lodged result, Jordan Sample, FY 2025-26"
    ]
    assert [i for i, _ in kind.candidates(s, "")] == [
        second.id,
        other.id,
    ]  # current ones only, newest first


def test_the_kind_is_registered_for_linking_documents() -> None:
    registry.register(link_kind.LODGED_KIND)
    assert registry.get("tax_lodged_result") is not None


# --- the API on the synthetic household ------------------------------------------------------


@pytest.fixture
def sample(tmp_path: Path) -> Iterator[TestClient]:
    data = tmp_path / "data"
    data.mkdir()
    fixtures = REPO / "fixtures" / "sample_household"
    for name in ("household.json", "lodged_results.json"):
        shutil.copy(fixtures / name, data / name)
    shutil.copytree(fixtures / "evidence", data / "evidence")
    settings = Settings(data_dir=data, port=8000, git_sha="dev", static_dir=None)
    with owner_client(create_app(settings)) as c:
        yield c


def jordan_id(client: TestClient) -> int:
    members_ = client.get("/api/household").json()["members"]
    return int(next(m["id"] for m in members_ if m["name"] == "Jordan Sample"))


def sheet(client: TestClient, member: int, fy: int = FY, **params: Any) -> dict[str, Any]:
    res = client.get(
        "/api/tax/lodged-results/worksheet",
        params={"member_id": member, "financial_year": fy, **params},
    )
    assert res.status_code == 200, res.text
    return dict(res.json())


def test_the_fixtures_notice_sits_close_to_the_estimate_with_each_difference_visible(
    sample: TestClient,
) -> None:
    got = sheet(sample, jordan_id(sample))
    assert got["problems"] == [] and got["member_name"] == "Jordan Sample"
    lines = {x["key"]: x for x in got["lines"]}
    assert [x["key"] for x in got["lines"]] == [
        "taxable_income",
        "tax_on_taxable_income",
        "offsets",
        "medicare_levy",
        "medicare_levy_surcharge",
        "help_repayment",
        "total_tax",
        "credits",
        "net_outcome",
    ]
    # the estimate (engine) and the amended notice (lodged), then engine less lodged
    assert (lines["medicare_levy"]["engine"], lines["medicare_levy"]["lodged"]) == (
        "1702.12",
        "1702.07",
    )
    assert lines["medicare_levy"]["difference"] == "0.05"
    assert lines["medicare_levy"]["status"] == "differs"
    assert lines["total_tax"]["difference"] == "0.05"  # 19292.51 less 19292.46
    assert lines["taxable_income"]["status"] == "matched"
    assert lines["taxable_income"]["difference"] == "0.00"
    # net outcome 557.49 against 557.54: within the notice's rounding, with the difference shown
    assert lines["net_outcome"]["status"] == "rounding"
    assert lines["net_outcome"]["difference"] == "-0.05"
    assert (got["matched_count"], got["differs_count"]) == (7, 2)
    # a lodged line the engine has no key for is named, not passed over
    assert got["unmatched"] == [
        {"name": "Sample processing adjustment", "amount": "25.00", "is_other": True}
    ]
    assert got["current"]["assessed_on"] == "2026-10-09" and got["current"]["current"] is True


def test_the_earlier_notice_is_in_the_history_and_retrievable_by_id(sample: TestClient) -> None:
    got = sheet(sample, jordan_id(sample))
    assert [(h["assessed_on"], h["current"]) for h in got["history"]] == [
        ("2026-10-09", True),
        ("2026-08-14", False),
    ]
    earlier = got["history"][1]
    assert earlier["superseded_by_id"] == got["current"]["id"]
    again = sample.get(f"/api/tax/lodged-results/{earlier['id']}")
    assert again.status_code == 200
    assert {i["key"]: i["amount"] for i in again.json()["items"]}["taxable_income"] == "85606.00"
    assert sample.get("/api/tax/lodged-results/9999").status_code == 404


def test_a_member_and_year_with_no_notice_shows_the_estimate_alone(sample: TestClient) -> None:
    got = sheet(sample, 1)  # Alex has no Lodged result
    assert got["current"] is None and got["history"] == [] and got["unmatched"] == []
    assert {x["status"] for x in got["lines"]} == {"not_lodged"}
    assert all(x["engine"] is not None and x["lodged"] is None for x in got["lines"])
    assert got["incomplete"] in (True, False) and got["engine_version"] is not None


def test_a_year_with_no_rule_figures_still_shows_the_notice_and_why_there_is_no_estimate(
    sample: TestClient,
) -> None:
    jordan = jordan_id(sample)
    made = sample.post(
        "/api/tax/lodged-results",
        json={
            "member_id": jordan,
            "financial_year": 2040,
            "assessed_on": "2040-09-01",
            "items": {"credits": "100.00"},
            "other": [{"name": "Sample line", "amount": "-3.00"}],
        },
    )
    assert made.status_code == 201, made.text
    got = sheet(sample, jordan, 2040)
    assert got["problems"] and "2039-40" in got["problems"][0]
    assert {x["status"] for x in got["lines"]} == {"no_estimate"}
    assert [(x["key"], x["lodged"]) for x in got["lines"] if x["lodged"]] == [("credits", "100.00")]
    assert got["unmatched"] == [{"name": "Sample line", "amount": "-3.00", "is_other": True}]


def test_entering_and_amending_through_the_api_audits_and_keeps_the_earlier(
    sample: TestClient,
) -> None:
    alex = 1
    body = {
        "member_id": alex,
        "financial_year": FY,
        "assessed_on": "2026-09-01",
        "items": {"taxable_income": "234601.00", "net_outcome": "-75914.59"},
        "other": [],
    }
    made = sample.post("/api/tax/lodged-results", json=body)
    assert made.status_code == 201, made.text
    assert made.json()["financial_year_label"] == "2025-26"
    again = sample.post("/api/tax/lodged-results", json=body)
    assert again.status_code == 422
    assert again.json()["errors"][0]["field"] == "financial_year"

    amended = sample.put(
        f"/api/tax/lodged-results/{made.json()['id']}",
        json={**body, "assessed_on": "2026-10-20", "items": {"taxable_income": "234600.00"}},
    )
    assert amended.status_code == 200, amended.text
    assert amended.json()["id"] != made.json()["id"] and amended.json()["current"] is True
    old = sample.get(f"/api/tax/lodged-results/{made.json()['id']}").json()
    assert old["current"] is False and old["superseded_by_id"] == amended.json()["id"]
    assert {i["key"]: i["amount"] for i in old["items"]}["net_outcome"] == "-75914.59"

    trail = sample.get("/api/audit", params={"entity_type": "tax_lodged_result"}).json()["items"]
    mine = [e for e in trail if e["source"] != "fixture"]
    assert [e["action"] for e in mine][::-1] == ["create", "supersede"]

    # amending an earlier (superseded) notice is refused, with the shared 422 shape
    stale = sample.put(f"/api/tax/lodged-results/{made.json()['id']}", json=body)
    assert stale.status_code == 422
    assert stale.json()["errors"] == [
        {"field": "id", "message": "Only the current Lodged result can be amended."}
    ]


def test_bad_input_over_the_api_is_a_422_in_the_shared_shape(sample: TestClient) -> None:
    res = sample.post(
        "/api/tax/lodged-results",
        json={
            "member_id": 1,
            "financial_year": FY,
            "items": {"credits": "12.345", "nonsense": "1.00"},
            "other": [{"name": "", "amount": "1.00"}],
        },
    )
    assert res.status_code == 422
    assert {e["field"] for e in res.json()["errors"]} == {
        "assessed_on",
        "items.credits",
        "items.nonsense",
        "other.0.name",
    }
    assert all(set(e) == {"field", "message"} for e in res.json()["errors"])
    float_amount = sample.post(  # a JSON number is not an amount
        "/api/tax/lodged-results",
        json={
            "member_id": 1,
            "financial_year": FY,
            "assessed_on": "2026-09-01",
            "items": {"credits": 1.5},
        },
    )
    assert float_amount.status_code == 422
    assert (
        sample.get(
            "/api/tax/lodged-results/worksheet", params={"member_id": 99, "financial_year": FY}
        ).status_code
        == 404
    )


def test_the_fixture_loads_once_and_never_again(tmp_path: Path, s: Session) -> None:
    members(s)
    data = tmp_path / "fx"
    data.mkdir()
    shutil.copy(REPO / "fixtures" / "sample_household" / "lodged_results.json", data / "x.json")
    assert lodged.load_fixture_lodged_results(s, data) == 0  # no file of the expected name
    shutil.copy(data / "x.json", data / lodged.FIXTURE_FILENAME)
    assert lodged.load_fixture_lodged_results(s, data) == 2  # an entry and its amendment
    assert lodged.load_fixture_lodged_results(s, data) == 0
    assert len(lodged.history(s, 2, FY)) == 2
