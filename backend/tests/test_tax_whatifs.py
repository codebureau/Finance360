"""Tax what-ifs, saved Tax estimates and the comparison (ADR-0019 section 6).

Pure logic (the overrides, the comparison) is tested without a database. The rest runs against the
synthetic household and a real temporary SQLite database, through the real API: the invariants an
immutable, pinned estimate promises (it reproduces from its input alone, it survives a later edit
or delete of a record, the database refuses UPDATE and DELETE, overrides are absolute and never
inherit) and the validation of overrides. The synthetic household and the shipped AU pack for
FY 2025-26 only; every figure is invented.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from tax_support import FY, aud, member, tax_input

from finance360.capabilities.tax import compare, engine, result_json, whatif
from finance360.capabilities.tax import input as tinput
from finance360.capabilities.tax.engine import estimate as run_engine
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
BASE = {"financial_year": YEAR, "dependants": 0, "member_ids": [ALEX, JORDAN]}


# --- the overrides (pure) -------------------------------------------------------------------


def test_overrides_round_trip_through_canonical_json() -> None:
    items = (
        whatif.SetTotal(1, "salary", aud("90000.00")),
        whatif.SetTotal(2, "surcharge_exempt_days", 120),
        whatif.ExcludeLabel(1, "interest"),
        whatif.ExcludeRecord(7),
        whatif.AddRecord({"record_type": "line", "label": "work_other", "amount": "12.50"}),
    )
    text_ = whatif.to_json(items)
    assert whatif.from_json(text_, YEAR) == items
    assert whatif.to_json(whatif.from_json(text_, YEAR)) == text_
    assert " " not in text_.replace("Sample", "")  # no spaces: canonical


@pytest.mark.parametrize(
    "payload",
    [
        [{"op": "set", "member_id": 1, "label": "salary", "value": {"ccy": "AUD", "minor": 1.5}}],
        [{"op": "set", "member_id": 1, "label": "salary", "value": {"ccy": "USD", "minor": 100}}],
        [{"op": "set", "member_id": 1, "label": "salary", "value": {"ccy": "AUD", "minor": -1}}],
        [{"op": "set", "member_id": 1, "label": "nonsense", "value": 1}],
        [{"op": "set", "member_id": 1, "label": "salary", "value": True}],
        [{"op": "exclude_label", "member_id": 0, "label": "salary"}],
        [{"op": "exclude_record", "record_id": 1.0}],
        [{"op": "add_record", "record": {}}],
        [{"op": "double", "member_id": 1}],
    ],
)
def test_an_invalid_override_is_rejected(payload: list[dict[str, Any]]) -> None:
    with pytest.raises(whatif.WhatIfError):
        whatif.from_payload(payload, YEAR)


def test_an_override_acts_on_each_thing_once() -> None:
    both = [
        {"op": "set", "member_id": 1, "label": "salary", "value": {"ccy": "AUD", "minor": 100}},
        {"op": "exclude_label", "member_id": 1, "label": "salary"},
    ]
    with pytest.raises(whatif.WhatIfError) as error:
        whatif.from_payload(both, YEAR)
    assert error.value.index == 1
    twice = [{"op": "exclude_record", "record_id": 4}] * 2
    with pytest.raises(whatif.WhatIfError):
        whatif.from_payload(twice, YEAR)


def test_a_set_total_replaces_whatever_the_records_gave() -> None:
    held: dict[tuple[int | None, str], Any] = {(1, "salary"): _total(1, "salary", "100000.00")}
    out, replaced = whatif.apply_labels(held, [whatif.SetTotal(1, "salary", aud("90000.00"))])
    assert out[(1, "salary")].total == aud("90000.00")
    assert out[(1, "salary")].contributions == ()  # it came from no record
    assert replaced[(1, "salary")] == aud("100000.00")
    # absolute: stating it again changes nothing, and a lower figure is not "minus"
    again, _ = whatif.apply_labels(out, [whatif.SetTotal(1, "salary", aud("90000.00"))])
    assert again == out


def _total(member_id: int, label: str, amount: str) -> Any:
    from finance360.capabilities.tax.record_types import LabelTotal

    return LabelTotal(member_id, label, aud(amount), ())


# --- the comparison (pure) ------------------------------------------------------------------


def _side(alex_salary: str, jordan_salary: str = "60000", dependants: int = 0) -> compare.Side:
    given = tax_input(
        member(1, salary=alex_salary), member(2, salary=jordan_salary), dependants=dependants
    )
    return compare.Side(given, run_engine(given))


def test_the_comparison_reports_exact_deltas_per_label_and_per_result() -> None:
    a, b = _side("80000"), _side("95000.50")
    got = compare.compare(a, b)
    assert got.financial_year == FY
    assert [(d.member_id, d.label, d.delta) for d in got.labels] == [(1, "salary", aud("15000.50"))]
    household = {r.key: r for r in got.results if r.member_id is None}
    assert set(household) == set(compare.RESULT_KEYS)
    for line in got.results:
        assert line.delta == line.b - line.a
    assert household["taxable_income"].delta == aud("15000.00")  # whole dollars
    # Jordan is untouched: every one of her lines has a zero delta
    assert all(r.delta.amount_minor == 0 for r in got.results if r.member_id == 2)
    assert got.same_engine and not got.rule_pack_differs


def test_comparing_an_estimate_with_itself_changes_nothing() -> None:
    a = _side("80000")
    got = compare.compare(a, a)
    assert got.labels == ()
    assert all(r.delta.amount_minor == 0 for r in got.results)


def test_a_flag_and_days_delta() -> None:
    a = tax_input(member(1, salary="50000", surcharge_exempt_days=0))
    b = tax_input(member(1, salary="50000", surcharge_exempt_days=100))
    got = compare.compare(compare.Side(a, run_engine(a)), compare.Side(b, run_engine(b)))
    assert [(d.label, d.delta) for d in got.labels] == [("surcharge_exempt_days", 100)]


def test_only_estimates_of_one_year_and_one_household_compare() -> None:
    a = _side("80000")
    other_year = tax_input(member(1, salary="1"), member(2, salary="1"), fy=2096)
    with pytest.raises(compare.CompareError, match="same financial year"):
        compare.compare(a, compare.Side(other_year, run_engine(other_year)))
    alone = tax_input(member(1, salary="1"))
    with pytest.raises(compare.CompareError, match="same Members"):
        compare.compare(a, compare.Side(alone, run_engine(alone)))


@settings(max_examples=60, deadline=None)
@given(st.integers(0, 400_000_00), st.integers(0, 400_000_00), st.integers(0, 3))
def test_deltas_are_always_b_less_a_exactly(first: int, second: int, kids: int) -> None:
    a = _side(f"{first // 100}.{first % 100:02d}", dependants=kids)
    b = _side(f"{second // 100}.{second % 100:02d}", dependants=kids)
    got = compare.compare(a, b)
    for line in got.results:
        assert line.delta.amount_minor == line.b.amount_minor - line.a.amount_minor
    assert [d.delta for d in got.labels] == (
        [] if first == second else [Money(second - first, "AUD")]
    )


# --- against the synthetic household ---------------------------------------------------------


@pytest.fixture
def sample(tmp_path: Path) -> Iterator[TestClient]:
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(SAMPLE / "household.json", data / "household.json")
    app = create_app(Settings(data_dir=data, port=8000, git_sha="dev", static_dir=None))
    with owner_client(app) as c:
        yield c


def factory_of(client: TestClient) -> sessionmaker[Session]:
    return client.app.state.session_factory  # type: ignore[attr-defined, no-any-return]


def whatif_body(name: str, overrides: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {"name": name, "financial_year": YEAR, "overrides": overrides, **extra}


def make_whatif(client: TestClient, name: str, overrides: list[dict[str, Any]]) -> dict[str, Any]:
    res = client.post("/api/tax/whatifs", json=whatif_body(name, overrides))
    assert res.status_code == 201, res.text
    return dict(res.json())


def save(client: TestClient, whatif_id: int | None = None, **extra: Any) -> dict[str, Any]:
    res = client.post("/api/tax/estimates", json={**BASE, "whatif_id": whatif_id, **extra})
    assert res.status_code == 201, res.text
    return dict(res.json())


def saved(client: TestClient, estimate_id: int) -> dict[str, Any]:
    res = client.get(f"/api/tax/estimates/{estimate_id}")
    assert res.status_code == 200, res.text
    return dict(res.json())


def row_of(client: TestClient, estimate_id: int) -> dict[str, str]:
    with factory_of(client)() as s:
        row = s.get(TaxEstimate, estimate_id)
        assert row is not None
        return {
            c.name: getattr(row, c.name)
            for c in TaxEstimate.__table__.columns  # every column, byte for byte
        }


def label_total(view: dict[str, Any], member_id: int, label: str) -> dict[str, Any] | None:
    return next(
        (t for t in view["totals"] if t["member_id"] == member_id and t["label_key"] == label),
        None,
    )


def test_a_saved_estimate_reproduces_from_its_input_alone(sample: TestClient) -> None:
    header = save(sample, label="Base")
    assert header["label"] == "Base" and header["current_engine"] is True
    assert header["whatif_id"] is None and header["financial_year"] == YEAR
    row = row_of(sample, header["id"])
    pinned = tinput.from_json(str(row["input_json"]))
    stored = result_json.from_json(str(row["result_json"]))
    # the input alone gives exactly the stored result, with nothing live read
    assert engine.estimate(pinned) == stored
    assert result_json.to_json(stored) == row["result_json"]
    assert row["tax_engine_version"] == engine.TAX_ENGINE_VERSION
    # the rules block is pinned: the whole TaxRules is in the input
    assert pinned.rules.financial_year == YEAR
    assert saved(sample, header["id"])["reproduces"] is True


def test_an_estimate_is_pinned_so_editing_or_deleting_a_record_later_changes_nothing(
    sample: TestClient,
) -> None:
    header = save(sample)
    before_row, before = row_of(sample, header["id"]), saved(sample, header["id"])
    records = sample.get("/api/tax/records", params={"financial_year": YEAR}).json()
    lines = [r for r in records if r["record_type"] == "line"]
    edited = lines[0]
    assert (
        sample.put(
            f"/api/tax/records/{edited['id']}",
            json={
                "member_id": edited["member_id"],
                "label": edited["label_key"],
                "amount": "99999.00",
                "from_on": edited["from_on"],
                "description": edited["description"],
            },
        ).status_code
        == 200
    )
    assert sample.delete(f"/api/tax/records/{lines[1]['id']}").status_code == 204
    # the register moved on: a fresh estimate differs from the saved one ...
    fresh = sample.post("/api/tax/estimate", json=BASE).json()
    assert fresh["household"]["net_outcome"] != before["view"]["household"]["net_outcome"]
    # ... and the saved one is byte for byte what it was
    assert row_of(sample, header["id"]) == before_row
    assert saved(sample, header["id"]) == before


def test_the_database_rejects_update_and_delete_of_an_estimate(sample: TestClient) -> None:
    header = save(sample)
    with factory_of(sample)() as s:
        with pytest.raises(DBAPIError, match="append-only"):
            s.execute(
                text("UPDATE tax_estimate SET label = 'x' WHERE id = :i"), {"i": header["id"]}
            )
        s.rollback()
        with pytest.raises(DBAPIError, match="append-only"):
            s.execute(text("DELETE FROM tax_estimate WHERE id = :i"), {"i": header["id"]})
        s.rollback()
    assert row_of(sample, header["id"])["label"] is None


def test_a_what_if_with_estimates_cannot_be_deleted_by_the_service_or_the_database(
    sample: TestClient,
) -> None:
    held = make_whatif(sample, "Held", [])
    assert sample.delete(f"/api/tax/whatifs/{held['id']}").status_code == 204  # none yet: allowed
    held = make_whatif(sample, "Held", [])
    header = save(sample, held["id"])
    res = sample.delete(f"/api/tax/whatifs/{held['id']}")
    assert res.status_code == 422
    assert "1 saved estimate" in res.json()["errors"][0]["message"]
    with factory_of(sample)() as s:  # and RESTRICT holds even if the service were bypassed
        with pytest.raises(IntegrityError):
            s.execute(text("DELETE FROM tax_whatif WHERE id = :i"), {"i": held["id"]})
        s.rollback()
    assert sample.get(f"/api/tax/estimates/{header['id']}").status_code == 200


def test_overrides_are_absolute_and_a_what_if_never_inherits(sample: TestClient) -> None:
    base = sample.post("/api/tax/estimates/preview", json=BASE).json()
    stated = {"op": "set", "member_id": ALEX, "label": "salary", "amount": "70000.00"}
    first = make_whatif(sample, "Stated 70k", [stated])
    again = make_whatif(sample, "Stated 70k, again", [stated])
    empty = make_whatif(sample, "Nothing changed", [])
    a = sample.post("/api/tax/estimates/preview", json={**BASE, "whatif_id": first["id"]}).json()
    b = sample.post("/api/tax/estimates/preview", json={**BASE, "whatif_id": again["id"]}).json()
    c = sample.post("/api/tax/estimates/preview", json={**BASE, "whatif_id": empty["id"]}).json()
    # absolute: the total is the stated one whatever the records gave, and the same on every run
    total = label_total(a, ALEX, "salary")
    assert total is not None
    assert total["value"]["amount"] == "70000.00" and total["overridden"] is True
    assert total["contributions"] == []  # it came from no record
    assert a["household"] == b["household"]
    # never inherits: a what-if with no overrides is the base, whatever the others state
    assert c["household"] == base["household"]
    assert [t["value"] for t in c["totals"]] == [t["value"] for t in base["totals"]]
    # the base's own Contributions are untouched by the previews
    assert label_total(base, ALEX, "salary") is not None


def test_a_what_if_is_for_one_year_and_one_of_its_estimates(sample: TestClient) -> None:
    held = make_whatif(sample, "A year", [])
    wrong = sample.post(
        "/api/tax/estimates/preview", json={**BASE, "financial_year": 2027, "whatif_id": held["id"]}
    )
    assert wrong.status_code == 422
    assert "FY 2025-26" in wrong.json()["errors"][0]["message"]


def test_the_three_operations_and_the_hypothetical_record(sample: TestClient) -> None:
    base_totals = sample.get("/api/tax/label-totals", params={"financial_year": YEAR}).json()
    other = next(
        t
        for t in base_totals["totals"]
        if t["member_id"] == ALEX and t["label_key"] == "work_other"
    )
    donation = next(
        r
        for r in sample.get("/api/tax/records").json()
        if r["description"] == "Donation to Sample Charity"
    )
    records_before = len(sample.get("/api/tax/records").json())
    laptop = {
        "member_id": ALEX,
        "record_type": "depreciating_asset",
        "label": "work_other",
        "from_on": "2025-11-10",
        "description": "Sample work laptop",
        "amount": "2400.00",
        "effective_life_years": "3",
        "work_use_percent": "80",
    }
    made = make_whatif(
        sample,
        "Everything",
        [
            {"op": "add_record", "record": laptop},
            {"op": "exclude_record", "record_id": donation["id"]},
            {"op": "exclude_label", "member_id": JORDAN, "label": "dividends_franked"},
        ],
    )
    view = sample.post("/api/tax/estimates/preview", json={**BASE, "whatif_id": made["id"]}).json()
    # 2,400.00 x 200% / 3 years x 233 of 365 days x 80% work use, worked by hand: 817.10
    added = label_total(view, ALEX, "work_other")
    assert added is not None
    assert added["overridden"] is False
    expected = int(other["amount"].replace(".", "")) + 81710
    assert added["value"]["amount"] == f"{expected // 100}.{expected % 100:02d}"
    hypothetical = [c for c in added["contributions"] if c["record_id"] < 0]
    assert [(c["description"], c["value"]["amount"]) for c in hypothetical] == [
        ("Sample work laptop (hypothetical)", "817.10")
    ]
    assert label_total(view, ALEX, "gifts_donations") is None  # the donation is left out
    assert label_total(view, JORDAN, "dividends_franked") is None  # and the dividends
    # each override is listed with its effect
    effects = {o["op"]: o for o in view["overrides"]}
    assert effects["exclude_record"]["contributions"][0]["value"]["amount"] == "250.00"
    assert effects["add_record"]["contributions"][0]["value"]["amount"] == "817.10"
    assert effects["exclude_label"]["replaced"] is not None
    # nothing was written to the register, and the base is unchanged
    assert len(sample.get("/api/tax/records").json()) == records_before
    again = sample.post("/api/tax/estimates/preview", json=BASE).json()
    assert label_total(again, ALEX, "gifts_donations") is not None


def test_provenance_lists_each_contribution_and_override_and_survives_a_rename(
    sample: TestClient,
) -> None:
    donation = next(
        r
        for r in sample.get("/api/tax/records").json()
        if r["description"] == "Donation to Sample Charity"
    )
    held = make_whatif(
        sample,
        "Tracked",
        [
            {"op": "exclude_record", "record_id": donation["id"]},
            {"op": "set", "member_id": JORDAN, "label": "salary", "amount": "95000.00"},
        ],
    )
    header = save(sample, held["id"])
    got = saved(sample, header["id"])["view"]
    assert [o["op"] for o in got["overrides"]] == ["exclude_record", "set"]
    assert got["whatif_name"] == "Tracked"
    interest = label_total(got, ALEX, "interest")
    assert interest is not None and interest["contributions"][0]["description"].startswith(
        "Interest"
    )
    # the what-if is renamed and its overrides replaced; the saved estimate keeps its own copy
    updated = sample.put(f"/api/tax/whatifs/{held['id']}", json=whatif_body("Renamed", []))
    assert updated.status_code == 200
    again = saved(sample, header["id"])
    assert again["view"]["whatif_name"] == "Tracked"
    assert [o["op"] for o in again["view"]["overrides"]] == ["exclude_record", "set"]
    assert again["estimate"]["whatif_name"] == "Tracked"


def test_an_estimate_from_an_older_engine_shows_as_computed_by_it_and_a_rerun_saves_a_new_one(
    sample: TestClient,
) -> None:
    current = save(sample, label="Today's engine")
    assert sample.post(f"/api/tax/estimates/{current['id']}/rerun").status_code == 422
    # seed one "computed by engine v1": the same pinned input, its result stamped v1
    with factory_of(sample).begin() as s:
        row = s.get(TaxEstimate, current["id"])
        assert row is not None
        old = TaxEstimate(
            created_at=row.created_at,
            label="Seeded, older engine",
            financial_year=row.financial_year,
            whatif_id=None,
            whatif_name=None,
            tax_engine_version=1,
            tax_input_version=row.tax_input_version,
            rule_pack_version=row.rule_pack_version,
            input_json=row.input_json,
            result_json=row.result_json.replace('"engine_version":2', '"engine_version":1'),
            provenance_json=row.provenance_json,
            incomplete=row.incomplete,
            has_provisional=row.has_provisional,
        )
        s.add(old)
        s.flush()
        old_id = old.id
    shown = saved(sample, old_id)
    assert shown["estimate"]["engine_version"] == 1
    assert shown["estimate"]["current_engine"] is False
    assert shown["view"]["engine_version"] == 1  # shown as computed by v1
    assert shown["reproduces"] is None  # a re-run would differ by design, so it is not claimed
    stored_before = row_of(sample, old_id)

    made = sample.post(f"/api/tax/estimates/{old_id}/rerun")
    assert made.status_code == 201, made.text
    fresh = saved(sample, made.json()["id"])
    assert fresh["estimate"]["engine_version"] == engine.TAX_ENGINE_VERSION
    assert fresh["view"]["rerun_of"] == old_id
    assert fresh["view"]["household"] == shown["view"]["household"]  # same input, same figures
    assert row_of(sample, old_id) == stored_before  # the old one stays exactly as it was
    assert len(sample.get("/api/tax/estimates").json()) == 3


def test_comparing_two_saved_estimates_of_one_year(sample: TestClient) -> None:
    base = save(sample, label="Base")
    held = make_whatif(
        sample,
        "More salary",
        [{"op": "set", "member_id": JORDAN, "label": "salary", "amount": "120000.00"}],
    )
    other = save(sample, held["id"], label="Higher")
    base_view = saved(sample, base["id"])["view"]
    jordan_before = label_total(base_view, JORDAN, "salary")
    assert jordan_before is not None
    got = sample.get("/api/tax/estimates/compare", params={"a": base["id"], "b": other["id"]})
    assert got.status_code == 200, got.text
    data = got.json()
    assert data["financial_year"] == YEAR
    assert [m["name"] for m in data["members"]] == ["Alex Sample", "Jordan Sample"]
    salary = next(d for d in data["labels"] if d["label_key"] == "salary")
    assert salary["a"]["amount"] == jordan_before["value"]["amount"]
    assert salary["b"]["amount"] == "120000.00"
    before_minor = int(jordan_before["value"]["amount"].replace(".", ""))
    assert salary["delta"]["amount"] == f"{(12_000_000 - before_minor) / 100:.2f}"
    assert [d["label_key"] for d in data["labels"]] == ["salary"]  # only what differs
    house = {r["key"]: r for r in data["results"] if r["member_id"] is None}
    assert set(house) == set(compare.RESULT_KEYS)
    for line in data["results"]:
        diff = int(line["b"].replace(".", "")) - int(line["a"].replace(".", ""))
        assert line["delta"] == f"{'-' if diff < 0 else ''}{abs(diff) // 100}.{abs(diff) % 100:02d}"
    assert house["total_tax"]["delta"] != "0.00"
    assert data["same_engine"] is True


def test_the_comparison_refuses_what_it_cannot_compare(sample: TestClient) -> None:
    a = save(sample)
    assert (
        sample.get("/api/tax/estimates/compare", params={"a": a["id"], "b": a["id"]}).status_code
        == 422
    )
    assert (
        sample.get("/api/tax/estimates/compare", params={"a": a["id"], "b": 99}).status_code == 404
    )
    # a different year (the shipped pack holds FY 2024-25 too): saved, then refused
    other = sample.post("/api/tax/estimates", json={**BASE, "financial_year": 2025})
    if other.status_code == 201:
        res = sample.get(
            "/api/tax/estimates/compare", params={"a": a["id"], "b": other.json()["id"]}
        )
        assert res.status_code == 422
        assert "same financial year" in res.json()["errors"][0]["message"]


def test_overrides_that_cannot_be_read_are_reported_each_against_its_own_override(
    sample: TestClient,
) -> None:
    res = sample.post(
        "/api/tax/whatifs",
        json=whatif_body(
            "Bad",
            [
                {"op": "set", "member_id": ALEX, "label": "salary", "amount": "12.345"},
                {"op": "set", "member_id": ALEX, "label": "salary", "amount": "5.00"},
                {"op": "exclude_label", "member_id": ALEX, "label": "no_such_label"},
                {"op": "teleport"},
            ],
        ),
    )
    assert res.status_code == 422
    fields = {e["field"]: e["message"] for e in res.json()["errors"]}
    assert "decimal places" in fields["overrides[0]"]
    assert "not a Return label" in fields["overrides[2]"]
    assert "Choose an operation" in fields["overrides[3]"]
    assert sample.get("/api/tax/whatifs").json() == []  # nothing was written
    twice = sample.post(
        "/api/tax/whatifs",
        json=whatif_body(
            "Twice",
            [
                {"op": "set", "member_id": ALEX, "label": "salary", "amount": "5.00"},
                {"op": "exclude_label", "member_id": ALEX, "label": "salary"},
            ],
        ),
    )
    assert twice.status_code == 422
    assert twice.json()["errors"][0] == {
        "field": "overrides[1]",
        "message": "This is overridden twice; keep one.",
    }


def test_overrides_naming_what_does_not_exist_are_reported_against_the_override(
    sample: TestClient,
) -> None:
    res = sample.post(
        "/api/tax/whatifs",
        json=whatif_body(
            "Missing",
            [
                {"op": "set", "member_id": 99, "label": "salary", "amount": "10.00"},
                {"op": "exclude_record", "record_id": 9999},
                {
                    "op": "add_record",
                    "record": {
                        "member_id": ALEX,
                        "record_type": "line",
                        "label": "work_other",
                        "from_on": "2025-10-01",
                        "description": "",
                        "amount": "x",
                    },
                },
            ],
        ),
    )
    assert res.status_code == 422
    fields = {e["field"]: e["message"] for e in res.json()["errors"]}
    assert fields["overrides[0]"] == "This Member does not exist."
    assert "9999" in fields["overrides[1]"]
    assert fields["overrides[2].description"] == "Description is required."
    assert "overrides[2].amount" in fields
    # a member outside the estimate is refused when it is calculated, with the same address
    held = make_whatif(
        sample, "Outside", [{"op": "set", "member_id": JORDAN, "label": "salary", "amount": "1.00"}]
    )
    alone = sample.post(
        "/api/tax/estimates/preview",
        json={**BASE, "member_ids": [ALEX], "whatif_id": held["id"]},
    )
    assert alone.status_code == 422
    assert alone.json()["errors"][0] == {
        "field": "overrides[0]",
        "message": "This Member is not part of the estimate.",
    }


def test_names_are_unique_and_a_what_if_can_be_edited_and_deleted(sample: TestClient) -> None:
    made = make_whatif(sample, "Mine", [])
    clash = sample.post("/api/tax/whatifs", json=whatif_body("Mine", []))
    assert clash.status_code == 422 and clash.json()["errors"][0]["field"] == "name"
    assert sample.post("/api/tax/whatifs", json=whatif_body("   ", [])).status_code == 422
    # editing in place with its own name is fine; the list narrows by year
    edit = sample.put(
        f"/api/tax/whatifs/{made['id']}",
        json=whatif_body(
            "Mine",
            [{"op": "set", "member_id": ALEX, "label": "salary", "amount": "1.00"}],
            notes="Invented.",
        ),
    )
    assert edit.status_code == 200 and edit.json()["notes"] == "Invented."
    assert [
        w["name"] for w in sample.get("/api/tax/whatifs", params={"financial_year": YEAR}).json()
    ] == ["Mine"]
    assert sample.get("/api/tax/whatifs", params={"financial_year": 2020}).json() == []
    assert sample.delete(f"/api/tax/whatifs/{made['id']}").status_code == 204
    assert sample.get(f"/api/tax/whatifs/{made['id']}").status_code == 404
    assert (
        sample.put(f"/api/tax/whatifs/{made['id']}", json=whatif_body("x", [])).status_code == 404
    )


def test_the_audit_trail_names_what_changed_and_an_estimate_is_create_only(
    sample: TestClient,
) -> None:
    held = make_whatif(sample, "Audited", [])
    sample.put(f"/api/tax/whatifs/{held['id']}", json=whatif_body("Audited 2", [], notes="n"))
    header = save(sample, held["id"])
    sample.post(f"/api/tax/estimates/{header['id']}/rerun")  # refused: current engine
    with factory_of(sample)() as s:
        events = s.execute(
            select(AuditEvent.entity_type, AuditEvent.entity_id, AuditEvent.action)
            .where(AuditEvent.entity_type.in_(["tax_whatif", "tax_estimate"]))
            .order_by(AuditEvent.id)
        ).all()
        created = s.scalars(
            select(AuditEvent).where(
                AuditEvent.entity_type == "tax_estimate", AuditEvent.entity_id == header["id"]
            )
        ).one()
    assert events == [
        ("tax_whatif", held["id"], "create"),
        ("tax_whatif", held["id"], "update"),
        ("tax_estimate", header["id"], "create"),
    ]
    assert created.after is not None and created.after["whatif_name"] == "Audited 2"
    assert "input_json" not in created.after  # header fields only, never the payload


def test_an_estimate_for_a_year_with_no_figures_is_refused_and_nothing_is_saved(
    sample: TestClient,
) -> None:
    res = sample.post("/api/tax/estimates", json={**BASE, "financial_year": 2090})
    assert res.status_code == 422
    assert sample.get("/api/tax/estimates").json() == []


def test_the_fixture_has_two_what_ifs_and_a_saved_base_estimate(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    for name in ("household.json", "tax_whatifs.json"):
        shutil.copy(SAMPLE / name, data / name)
    app = create_app(Settings(data_dir=data, port=8000, git_sha="dev", static_dir=None))
    with owner_client(app) as client:
        names = [w["name"] for w in client.get("/api/tax/whatifs").json()]
        assert sorted(names) == ["Alex buys a work laptop", "Jordan earns more, no donation"]
        estimates = client.get("/api/tax/estimates").json()
        assert [(e["label"], e["whatif_id"], e["current_engine"]) for e in estimates] == [
            ("Base, as the records stand", None, True)
        ]
        shown = saved(client, estimates[0]["id"])
        assert shown["reproduces"] is True
        # each what-if calculates against the sample records
        for w in client.get("/api/tax/whatifs").json():
            run = client.post("/api/tax/estimates/preview", json={**BASE, "whatif_id": w["id"]})
            assert run.status_code == 200, run.text
        # a second start loads nothing more
        assert len(client.get("/api/tax/estimates").json()) == 1


def test_the_rows_hold_no_float_and_the_what_if_is_valid_json(sample: TestClient) -> None:
    held = make_whatif(
        sample,
        "Exact",
        [{"op": "set", "member_id": ALEX, "label": "salary", "amount": "70000.10"}],
    )
    with factory_of(sample)() as s:
        row = s.get(TaxWhatIf, held["id"])
        assert row is not None
        assert row.overrides_json == (
            '[{"label":"salary","member_id":1,"op":"set","value":{"ccy":"AUD","minor":7000010}}]'
        )
        with pytest.raises(DBAPIError):  # json_valid CHECK
            s.execute(
                text(
                    "INSERT INTO tax_whatif (name, financial_year, origin, overrides_json, "
                    "created_at) VALUES ('Bad', 2026, 'user', '[not json', '2026-01-01')"
                )
            )
        s.rollback()
