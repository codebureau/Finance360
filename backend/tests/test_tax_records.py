"""Tax records and Label totals (ADR-0019 sections 2 and 5).

Unit tests of the Record types (pure), a Hypothesis property for the Label total, and integration
tests through the API against a real temporary SQLite database holding the synthetic household's
invented Tax records (FY 2025-26, ending 2026). Every figure is invented.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from finance360.capabilities.tax import record_types
from finance360.capabilities.tax.input import InputError, loads
from finance360.capabilities.tax.record_types import fact, line
from finance360.capabilities.tax.record_types.base import Record
from finance360.config import Settings
from finance360.core.money import Money, parse_amount
from finance360.core.rate import PPM
from finance360.main import create_app
from support import TestClient, owner_client

REPO = Path(__file__).resolve().parents[2]
ALEX, JORDAN = 1, 2
FY = 2026  # FY 2025-26: 1 July 2025 to 30 June 2026


def aud(text: str) -> Money:
    return parse_amount(text, "AUD")


def a_line(
    record_id: int,
    amount: str,
    *,
    ppm: int | None = None,
    label: str = "work_other",
    member: int = ALEX,
    from_on: date = date(2025, 8, 1),
    to_on: date | None = None,
) -> Record:
    payload: dict[str, Any] = {
        "label": label,
        "amount": {"minor": aud(amount).amount_minor, "ccy": "AUD"},
    }
    if ppm is not None:
        payload["work_use_ppm"] = ppm
    return Record(record_id, member, "line", from_on, to_on, line.parse(payload))


# --- the Record types (pure) ----------------------------------------------------------------


def test_a_work_use_share_counts_that_part_of_the_amount() -> None:
    record = a_line(1, "1200.00", ppm=400_000)
    (contribution,) = record_types.contributions_of(record)
    assert (contribution.record_id, contribution.label) == (1, "work_other")
    assert contribution.value == aud("480.00")


def test_without_a_share_the_whole_amount_counts() -> None:
    (contribution,) = record_types.contributions_of(a_line(7, "250.00", label="gifts_donations"))
    assert contribution.value == aud("250.00")


def test_a_contribution_is_rounded_once_per_record_not_once_per_total() -> None:
    """Golden: 0.05 at 50% is 2.5 minor units, which rounds half to even to 0.02. Two such records
    total 0.04. Summing the amounts first and scaling once would give 0.05, so this fails if the
    rounding is moved from the record to the total."""
    records = [a_line(1, "0.05", ppm=500_000), a_line(2, "0.05", ppm=500_000)]
    (total,) = record_types.label_totals(records, FY).values()
    assert total.total == aud("0.04")
    assert [c.value for c in total.contributions] == [aud("0.02"), aud("0.02")]
    # and a half the other way rounds up to even: 0.15 at 50% is 7.5 minor units, so 0.08
    (other,) = record_types.label_totals([a_line(3, "0.15", ppm=500_000)], FY).values()
    assert other.total == aud("0.08")


@pytest.mark.parametrize(
    ("from_on", "to_on", "counts"),
    [
        (date(2025, 6, 30), None, False),  # the day before the year
        (date(2025, 7, 1), None, True),  # the first day
        (date(2026, 6, 30), None, True),  # the last day
        (date(2026, 7, 1), None, False),  # the day after
        (date(2025, 6, 1), date(2025, 6, 30), False),  # a span that ends the day before
        (date(2025, 6, 1), date(2025, 7, 1), True),  # a span that reaches the first day
        (date(2026, 6, 30), date(2027, 3, 1), True),  # a span that starts on the last day
        (date(2026, 7, 1), date(2027, 3, 1), False),  # a span that starts the day after
        (date(2024, 1, 1), date(2027, 12, 31), True),  # a span that covers the whole year
    ],
)
def test_a_record_counts_when_its_span_overlaps_the_year(
    from_on: date, to_on: date | None, counts: bool
) -> None:
    record = a_line(1, "100.00", from_on=from_on, to_on=to_on)
    totals = record_types.label_totals([record], FY)
    assert (len(totals) == 1) is counts
    if counts:  # a record counts whole: it is never apportioned across the years it spans
        assert next(iter(totals.values())).total == aud("100.00")


def test_a_float_anywhere_in_a_payload_is_rejected() -> None:
    good = {"label": "salary", "amount": {"minor": 1000, "ccy": "AUD"}}
    assert line.parse(good).amount == aud("10.00")
    with pytest.raises(InputError):
        line.parse({**good, "amount": {"minor": 1000.0, "ccy": "AUD"}})
    with pytest.raises(InputError):
        line.parse({**good, "work_use_ppm": 500000.0})
    with pytest.raises(InputError):
        fact.parse({"label": "surcharge_exempt_days", "value": 30.0})
    with pytest.raises(InputError):  # and in stored JSON text, before it is even a value
        loads('{"label": "salary", "amount": {"minor": 10.5, "ccy": "AUD"}}')


@pytest.mark.parametrize(
    "payload",
    [
        {"label": "nonsense", "amount": {"minor": 1, "ccy": "AUD"}},
        {"label": "help_debt", "amount": {"minor": 1, "ccy": "AUD"}},  # a flag takes a fact
        {"label": "salary", "amount": {"minor": -1, "ccy": "AUD"}},
        {"label": "salary", "amount": {"minor": 1, "ccy": "USD"}},
        {"label": "salary", "amount": {"minor": 1, "ccy": "AUD"}, "work_use_ppm": 1_000_001},
        {"label": "salary", "amount": {"minor": 1, "ccy": "AUD"}, "work_use_ppm": -1},
    ],
)
def test_a_line_that_does_not_fit_its_label_is_rejected(payload: dict[str, Any]) -> None:
    with pytest.raises(InputError):
        line.parse(payload)


def test_a_fact_takes_a_flag_or_days_and_nothing_else() -> None:
    assert fact.parse({"label": "help_debt", "value": True}).value is True
    assert fact.parse({"label": "surcharge_exempt_days", "value": 90}).value == 90
    for payload in (
        {"label": "help_debt", "value": 1},  # a flag is true or false, not a number
        {"label": "surcharge_exempt_days", "value": True},
        {"label": "surcharge_exempt_days", "value": -1},
        {"label": "salary", "value": 5},  # money takes a line
    ):
        with pytest.raises(InputError):
            fact.parse(payload)


def test_flags_and_days_add_up_into_a_label_total() -> None:
    def fact_record(record_id: int, label: str, value: bool | int) -> Record:
        parsed = fact.parse({"label": label, "value": value})
        return Record(record_id, ALEX, "fact", date(2025, 9, 1), None, parsed)

    totals = record_types.label_totals(
        [
            fact_record(1, "surcharge_exempt_days", 100),
            fact_record(2, "surcharge_exempt_days", 65),
            fact_record(3, "help_debt", False),
            fact_record(4, "help_debt", True),
        ],
        FY,
    )
    assert totals[(ALEX, "surcharge_exempt_days")].total == 165
    assert totals[(ALEX, "help_debt")].total is True


@given(
    st.lists(
        st.tuples(
            st.integers(min_value=0, max_value=10**12),
            st.one_of(st.none(), st.integers(min_value=0, max_value=PPM)),
        ),
        max_size=12,
    )
)
def test_a_label_total_is_the_exact_sum_of_its_contributions(
    entries: list[tuple[int, int | None]],
) -> None:
    from finance360.core.money import scale

    records = [
        Record(
            i,
            ALEX,
            "line",
            date(2025, 8, 1),
            None,
            line.parse(
                {
                    "label": "work_other",
                    "amount": {"minor": minor, "ccy": "AUD"},
                    **({} if ppm is None else {"work_use_ppm": ppm}),
                }
            ),
        )
        for i, (minor, ppm) in enumerate(entries, start=1)
    ]
    totals = record_types.label_totals(records, FY)
    if not records:
        assert totals == {}
        return
    total = totals[(ALEX, "work_other")]
    expected = [
        minor if ppm is None else scale(Money(minor, "AUD"), ppm, PPM).amount_minor
        for minor, ppm in entries
    ]
    assert [c.value.amount_minor for c in total.contributions] == expected  # type: ignore[union-attr]
    assert total.total == Money(sum(expected), "AUD")
    assert [c.record_id for c in total.contributions] == list(range(1, len(entries) + 1))


# --- the register, through the API ------------------------------------------------------------


@pytest.fixture
def sample(tmp_path: Path) -> Iterator[TestClient]:
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(REPO / "fixtures" / "sample_household" / "household.json", data / "household.json")
    settings = Settings(data_dir=data, port=8000, git_sha="dev", static_dir=None)
    with owner_client(create_app(settings)) as c:
        yield c


def records(client: TestClient, **params: Any) -> list[dict[str, Any]]:
    res = client.get("/api/tax/records", params=params)
    assert res.status_code == 200, res.text
    return res.json()


def totals(
    client: TestClient, member_id: int | None = None
) -> dict[tuple[int, str], dict[str, Any]]:
    params: dict[str, Any] = {"financial_year": FY}
    if member_id is not None:
        params["member_id"] = member_id
    res = client.get("/api/tax/label-totals", params=params)
    assert res.status_code == 200, res.text
    return {(t["member_id"], t["label_key"]): t for t in res.json()["totals"]}


def a_body(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "member_id": ALEX,
        "record_type": "line",
        "label": "work_other",
        "amount": "200.00",
        "work_use_percent": "50",
        "from_on": "2026-01-10",
        "description": "Invented tools",
    }
    base.update(changes)
    return base


def test_the_synthetic_household_has_tax_records_for_both_members(sample: TestClient) -> None:
    everything = records(sample)
    assert {r["member_name"] for r in everything} == {"Alex Sample", "Jordan Sample"}
    assert {r["record_type"] for r in everything} == {
        "line",
        "fact",
        "income_statement",
        "depreciating_asset",
        "home_office_hours",
        "car_kilometres",
    }
    in_year = records(sample, financial_year=FY)
    # the laptop is dated in the year before, Jordan has an income statement for FY 2023-24, Alex
    # one for FY 2024-25 (the comparison's previous year) and a car record for FY 2026-27 (it waits
    # for a Rule figure)
    assert len(in_year) == len(everything) - 4
    assert "Work laptop" not in " ".join(r["description"] for r in in_year)


def test_a_joint_cost_is_entered_as_each_members_share(sample: TestClient) -> None:
    fees = records(sample, label="tax_affairs", financial_year=FY)
    assert {(r["member_name"], r["amount"]) for r in fees} == {
        ("Alex Sample", "320.00"),
        ("Jordan Sample", "320.00"),
    }


def test_a_label_total_drills_down_to_its_records(sample: TestClient) -> None:
    home_internet = next(r for r in records(sample) if "internet" in r["description"])
    assert (home_internet["amount"], home_internet["work_use_percent"]) == ("1200.00", "40")
    assert home_internet["counted"] == "480.00"
    total = totals(sample)[(ALEX, "work_other")]
    assert total["amount"] == "480.00"
    assert [c["record_id"] for c in total["contributions"]] == [home_internet["id"]]
    # the laptop (60% of 1,800.00) is Jordan's and outside the year: it contributes to nothing.
    # Jordan's work_other is the standing desk (50% of 800.00, claimed in full) and the casual home
    # office hours (65.50 hours at 0.70 = 45.85).
    assert totals(sample)[(JORDAN, "work_other")]["amount"] == "445.85"


def test_a_label_total_adds_each_contribution_and_follows_edits_and_deletes(
    sample: TestClient,
) -> None:
    extra = sample.post("/api/tax/records", json=a_body()).json()
    assert totals(sample)[(ALEX, "work_other")]["amount"] == "580.00"  # 480.00 + 50% of 200.00
    sample.put(f"/api/tax/records/{extra['id']}", json=a_body(amount="300.00"))
    assert totals(sample)[(ALEX, "work_other")]["amount"] == "630.00"
    assert sample.delete(f"/api/tax/records/{extra['id']}").status_code == 204
    assert totals(sample)[(ALEX, "work_other")]["amount"] == "480.00"


def test_the_fact_is_a_flag_in_the_totals(sample: TestClient) -> None:
    flag = totals(sample)[(ALEX, "help_debt")]
    assert (flag["kind"], flag["flag"], flag["amount"]) == ("flag", True, None)
    (fact_record,) = [r for r in records(sample) if r["record_type"] == "fact"]
    assert (fact_record["value"], fact_record["counted"]) == (True, None)


def test_the_register_narrows_by_member_year_label_and_words(sample: TestClient) -> None:
    assert {r["member_id"] for r in records(sample, member_id=JORDAN)} == {JORDAN}
    assert {r["label_key"] for r in records(sample, label="franking_credits")} == {
        "franking_credits"
    }
    # the desk and the casual home office hours (the laptop is in FY - 1)
    assert len(records(sample, financial_year=FY, member_id=JORDAN, label="work_other")) == 2
    assert len(records(sample, financial_year=FY - 1, member_id=JORDAN, label="work_other")) == 1
    # words match the description, notes, label name and return item, all words together
    assert [r["description"] for r in records(sample, q="sample charity")] == [
        "Donation to Sample Charity"
    ]
    assert len(records(sample, q="gifts")) == 1  # the label's name, not in the description
    assert len(records(sample, q="D5")) == 4  # the return item of Other work-related expenses
    assert len(records(sample, q="internet 40%")) == 1  # the notes are searched too
    assert len(records(sample, q="internet charity")) == 0  # all words must match
    # the Member's name and the label: both of Jordan's income statements (FY 2023-24 and 2025-26)
    assert len(records(sample, q="Jordan salary")) == 2


def test_a_record_spanning_years_is_found_in_each(sample: TestClient) -> None:
    sample.post(
        "/api/tax/records",
        json=a_body(
            label="rent_gross",
            amount="5000.00",
            work_use_percent=None,
            from_on="2026-03-01",
            to_on="2026-09-30",
            description="Invented tenancy",
        ),
    )
    for year in (FY, FY + 1):
        assert [
            r["description"] for r in records(sample, financial_year=year, label="rent_gross")
        ] == ["Invented tenancy"]
    assert records(sample, financial_year=FY - 1, label="rent_gross") == []
    assert records(sample, financial_year=FY + 2, label="rent_gross") == []


def test_edits_and_deletes_appear_in_the_audit_trail(sample: TestClient) -> None:
    made = sample.post("/api/tax/records", json=a_body()).json()
    record_id = made["id"]
    edited = sample.put(f"/api/tax/records/{record_id}", json=a_body(amount="260.00"))
    assert edited.status_code == 200 and edited.json()["id"] == record_id  # in place
    sample.put(f"/api/tax/records/{record_id}", json=a_body(amount="260.00"))  # no change: no event
    assert sample.delete(f"/api/tax/records/{record_id}").status_code == 204

    trail = sample.get(
        "/api/audit", params={"entity_type": "tax_record", "entity_id": record_id}
    ).json()["items"]
    assert [e["action"] for e in trail][::-1] == ["create", "update", "delete"]  # no-op edit: none
    update = next(e for e in trail if e["action"] == "update")
    assert update["changes"] == [{"field": "Amount", "before": "AUD 200.00", "after": "AUD 260.00"}]
    created = next(e for e in trail if e["action"] == "create")
    shown = {c["field"]: c["after"] for c in created["changes"]}
    assert (shown["Amount"], shown["Work use percent"], shown["Label"]) == (
        "AUD 200.00",
        "50",
        "work_other",
    )
    deleted = next(e for e in trail if e["action"] == "delete")
    assert deleted["summary"].startswith("Deleted Tax record 'Invented tools'")
    assert {c["field"]: c["before"] for c in deleted["changes"]}["Amount"] == "AUD 260.00"


def test_the_fixture_records_are_audited_as_the_fixture(sample: TestClient) -> None:
    trail = sample.get("/api/audit", params={"entity_type": "tax_record"}).json()["items"]
    assert len(trail) == len(records(sample))
    assert {e["source"] for e in trail} == {"fixture"}


def test_a_float_is_rejected_by_the_api(sample: TestClient) -> None:
    for changes in (
        {"amount": 12.5},  # a JSON number, not an amount text
        {"work_use_percent": 40.5},
        {
            "record_type": "fact",
            "label": "surcharge_exempt_days",
            "amount": None,
            "work_use_percent": None,
            "value": 30.5,
        },
    ):
        res = sample.post("/api/tax/records", json=a_body(**changes))
        assert res.status_code == 422, changes
        assert all(set(e) == {"field", "message"} for e in res.json()["errors"])
    assert len(records(sample, q="Invented tools")) == 0


def test_every_problem_is_collected_against_its_own_field(sample: TestClient) -> None:
    res = sample.post(
        "/api/tax/records",
        json={
            "member_id": 99,
            "record_type": "line",
            "label": "nonsense",
            "amount": "12.345",
            "from_on": "2026-02-01",
            "to_on": "2026-01-01",
            "description": "  ",
        },
    )
    assert res.status_code == 422
    assert {e["field"] for e in res.json()["errors"]} == {
        "member_id",
        "label",
        "to_on",
        "description",
    }
    res = sample.post("/api/tax/records", json=a_body(amount="-5.00", work_use_percent="101"))
    assert {e["field"] for e in res.json()["errors"]} == {"amount", "work_use_percent"}
    res = sample.post("/api/tax/records", json=a_body(amount=None))
    assert [e["field"] for e in res.json()["errors"]] == ["amount"]
    res = sample.post("/api/tax/records", json=a_body(label="help_debt"))
    assert [e["field"] for e in res.json()["errors"]] == ["label"]  # a flag takes a fact
    res = sample.post(
        "/api/tax/records",
        json=a_body(
            record_type="fact", label="help_debt", amount=None, work_use_percent=None, value=3
        ),
    )
    assert [e["field"] for e in res.json()["errors"]] == ["value"]
    res = sample.post("/api/tax/records", json=a_body(record_type="home_office"))
    assert [e["field"] for e in res.json()["errors"]] == ["record_type"]
    res = sample.post("/api/tax/records", json=a_body(member_id=None))
    assert [e["field"] for e in res.json()["errors"]] == ["member_id"]


def test_a_missing_record_is_a_404(sample: TestClient) -> None:
    assert sample.get("/api/tax/records/9999").status_code == 404
    assert sample.put("/api/tax/records/9999", json=a_body()).status_code == 404
    assert sample.delete("/api/tax/records/9999").status_code == 404


def test_a_member_with_tax_records_cannot_be_deleted(sample: TestClient) -> None:
    sam = sample.post(
        "/api/household/members", json={"name": "Sam Sample", "date_of_birth": "1990-01-01"}
    ).json()["id"]
    made = sample.post("/api/tax/records", json=a_body(member_id=sam)).json()
    res = sample.delete(f"/api/household/members/{sam}")
    assert res.status_code == 409 and "they have Tax records" in res.json()["message"]
    assert sample.delete(f"/api/tax/records/{made['id']}").status_code == 204
    assert sample.delete(f"/api/household/members/{sam}").status_code == 204


def test_the_register_needs_no_rule_pack_year(sample: TestClient) -> None:
    """A year the Rule pack holds no figures for still has a register and Label totals."""
    sample.post("/api/tax/records", json=a_body(from_on="2015-03-01", description="Invented, old"))
    res = sample.get("/api/tax/label-totals", params={"financial_year": 2015})
    assert res.status_code == 200
    assert [t["amount"] for t in res.json()["totals"]] == ["100.00"]


def test_the_estimate_screen_reads_the_registers_totals(sample: TestClient) -> None:
    """FY 2026 is a year the shipped AU pack holds figures for, so the synthetic household can be
    estimated from its own Tax records."""
    res = sample.post(
        "/api/tax/estimate",
        json={"financial_year": FY, "dependants": 0, "member_ids": [ALEX, JORDAN]},
    )
    assert res.status_code == 200, res.text
    alex, jordan = res.json()["members"]
    # Alex: salary and interest, plus 6,000.00 of business income less the camera's 495.89 and the
    # home office's 1,037.40 and the car's 849.55 (965.4 km at 0.88)
    assert (alex["total_income"], jordan["total_income"]) == ("235650.53", "86500.00")
    assert res.json()["incomplete"] is False
    # Alex: 480.00 of internet, 250.00 donated, 320.00 of agent fee
    assert alex["total_deductions"] == "1050.00"
    # Jordan: 320.00 of agent fee, the desk's 400.00 (800.00 at 50%, claimed in full) and the
    # casual home office hours' 45.85 (65.50 hours at 0.70) and the car's 679.18 (771.8 km at 0.88)
    assert jordan["total_deductions"] == "1445.03"
    # an edit to a record moves the estimate
    salary = next(r for r in records(sample, member_id=ALEX, label="salary"))
    sample.put(
        f"/api/tax/records/{salary['id']}",
        json=a_body(
            label="salary",
            amount="130000.00",
            work_use_percent=None,
            from_on="2025-07-01",
            to_on="2026-06-30",
            description=salary["description"],
        ),
    )
    again = sample.post(
        "/api/tax/estimate", json={"financial_year": FY, "dependants": 0, "member_ids": [ALEX]}
    ).json()
    assert again["members"][0]["total_income"] == "245650.53"
