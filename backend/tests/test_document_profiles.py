"""Saved Document profiles (ADR-0027 section 5) through the API and the store, against a
real temporary SQLite database and the synthetic household. Every profile, file and value here is
invented.

Invariant work: the table is append-only, the registry overrides and restores a shipped key, a bad
saved row can never stop the app starting, and a test snapshot never holds a figure.
"""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from finance360.capabilities.auth.types import Principal, Role
from finance360.capabilities.documents import registry, store
from finance360.capabilities.documents.reading import Found, NotFound, Reading
from finance360.config import Settings
from finance360.core.db import db_path, make_engine, make_session_factory
from finance360.core.migrate import migrate
from finance360.core.models.audit import AuditEvent
from finance360.core.models.document_profile import DocumentProfile
from finance360.core.money import Money
from finance360.main import create_app
from finance360.plugins.documents.ato_income_statement import PROFILE
from support import TestClient, as_owner, owner_client

REPO = Path(__file__).resolve().parents[2]
SAMPLE = REPO / "fixtures" / "sample_household"
SHIPPED_JSON = (
    REPO
    / "backend"
    / "finance360"
    / "plugins"
    / "documents"
    / "ato_income_statement"
    / "profile.json"
).read_text(encoding="utf-8")
SAMPLE_PDF = (SAMPLE / "documents" / "ato_income_statement_sample.pdf").read_bytes()
BANK_PDF = (REPO / "fixtures" / "documents" / "sample_bank_statement.pdf").read_bytes()
VARIANT_KEY = "ato-income-statement-sample-variant"
SHIPPED_KEY = "ato-income-statement"
#: The shipped profile's version, so a bump needs no edit here; a saved override is the next one.
SHIPPED_VERSION = PROFILE.version
URL = "/api/document-profiles"


def profile_text(**changes: Any) -> str:
    """The shipped ATO profile with ``changes`` applied: a draft that reads the sample statement.
    Version 1 unless told otherwise, as the rows these tests plant are."""
    return json.dumps({**json.loads(SHIPPED_JSON), "version": 1, **changes})


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(SAMPLE / "household.json", data / "household.json")
    shutil.copytree(SAMPLE / "evidence", data / "evidence")
    shutil.copytree(SAMPLE / "documents", data / "documents")
    return Settings(data_dir=data, port=8000, git_sha="dev", static_dir=None)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with owner_client(create_app(settings)) as c:
        yield c
    assert registry.saved_profile(VARIANT_KEY) is None  # the registry is left clean


@pytest.fixture
def sessions(settings: Settings) -> Iterator[Any]:
    engine = make_engine(db_path(settings.data_dir))
    yield make_session_factory(engine)
    engine.dispose()


def add_document(client: TestClient, content: bytes, media_type: str, name: str) -> int:
    res = client.post(
        "/api/library/documents",
        params={"file_name": name},
        content=content,
        headers={"Content-Type": media_type},
    )
    assert res.status_code in (200, 201), res.text
    return int(res.json()["document"]["id"])


def sample_document(client: TestClient) -> int:
    """The Library document the fixture's Proposed profile was tested on."""
    found = [
        d
        for d in client.get("/api/library/documents").json()["items"]
        if d["file_name"] == "ato_income_statement_sample.pdf"
    ]
    assert len(found) == 1
    return int(found[0]["id"])


def save(client: TestClient, text: str, document_id: int) -> Any:
    return client.post(URL, json={"profile_json": text, "document_id": document_id})


def summaries(client: TestClient) -> dict[str, Any]:
    return {p["key"] + f"@{p['version']}": p for p in client.get(URL).json()}


def reading_profile(client: TestClient) -> tuple[str, int]:
    """Which profile reads the sample statement now."""
    res = client.post(
        "/api/documents/read",
        params={"target": "tax.income_statement"},
        content=SAMPLE_PDF,
        headers={"Content-Type": "application/pdf"},
    )
    assert res.status_code == 200, res.text
    return res.json()["profile_key"], res.json()["profile_version"]


def variant_id(client: TestClient) -> int:
    return int(summaries(client)[f"{VARIANT_KEY}@1"]["id"])


def row_count(sessions: Any) -> int:
    with sessions() as session:
        return int(session.scalar(select(func.count()).select_from(DocumentProfile)) or 0)


# --- the list, and the fixture's Proposed profile ----------------------------------------------


def test_the_list_shows_the_shipped_profile_and_the_fixtures_proposed_one(
    client: TestClient,
) -> None:
    found = summaries(client)
    shipped = found[f"{SHIPPED_KEY}@{SHIPPED_VERSION}"]
    assert (shipped["status"], shipped["source"], shipped["id"]) == ("shipped", "shipped", None)
    assert shipped["target"] == "tax.income_statement"
    assert "gross_payments" in shipped["fills"] and shipped["sums"]
    proposed = found[f"{VARIANT_KEY}@1"]
    assert (proposed["status"], proposed["source"]) == ("proposed", "pasted")
    assert proposed["overrides_shipped"] is False

    detail = client.get(f"{URL}/{proposed['id']}").json()
    assert detail["test_document"]["file_name"] == "ato_income_statement_sample.pdf"
    assert detail["test_snapshot"]["outcome"] == "ok"
    assert json.loads(detail["profile_json"])["key"] == VARIANT_KEY
    assert client.get(f"{URL}/shipped/{SHIPPED_KEY}").json()["status"] == "shipped"
    assert client.get(f"{URL}/shipped/nope").status_code == 404


def test_a_proposed_profile_reads_nothing(client: TestClient) -> None:
    assert reading_profile(client) == (SHIPPED_KEY, SHIPPED_VERSION)
    assert registry.saved_profile(VARIANT_KEY) is None


# --- paste-and-test ----------------------------------------------------------------------------


def test_paste_and_test_stores_nothing_and_returns_the_reading(
    client: TestClient, sessions: Any
) -> None:
    doc = sample_document(client)
    rows = row_count(sessions)
    with sessions() as session:
        audits = session.scalar(select(func.count()).select_from(AuditEvent))
    res = client.post(
        f"{URL}/test",
        json={"profile_json": profile_text(key="pasted-draft"), "document_id": doc},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["acceptable"] is True
    assert body["profile"]["status"] == "draft" and body["profile"]["id"] is None
    assert {f["key"]: f["value"] for f in body["reading"]["found"]}["gross_payments"] == "84500.00"
    assert body["also_claimed_by"] == [SHIPPED_KEY]  # only registered profiles count
    assert row_count(sessions) == rows
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == audits


@pytest.mark.parametrize(
    "text",
    [
        "not json at all",
        '{"key": "x"}',
        profile_text(target="no.such_form"),
        profile_text(claim=["Account 1234567 statement"]),
        profile_text(
            fields=[{"key": "invented_field", "type": "money", "labels": ["A"], "pair": "same_row"}]
        ),
        profile_text(
            fields=[{"key": "gross_payments", "type": "text", "labels": ["A"], "pair": "same_row"}]
        ),
    ],
)
def test_an_invalid_draft_is_refused_in_the_shared_422_shape(
    client: TestClient, sessions: Any, text: str
) -> None:
    doc = sample_document(client)
    rows = row_count(sessions)
    for res in (
        client.post(f"{URL}/test", json={"profile_json": text, "document_id": doc}),
        save(client, text, doc),
    ):
        assert res.status_code == 422, res.text
        [error] = res.json()["errors"]
        assert error["field"] == "profile" and error["message"]
        assert "1234567" not in error["message"]
    assert row_count(sessions) == rows


def test_a_profile_that_does_not_read_the_document_is_not_saved(
    client: TestClient, sessions: Any
) -> None:
    bank = add_document(client, BANK_PDF, "application/pdf", "bank.pdf")
    rows = row_count(sessions)
    res = save(client, profile_text(key="reads-nothing"), bank)
    assert res.status_code == 422
    assert res.json()["errors"][0]["field"] == "profile"
    assert row_count(sessions) == rows
    assert save(client, profile_text(), 9999).status_code == 404


def test_saving_makes_a_proposed_profile_with_source_pasted(
    client: TestClient, sessions: Any
) -> None:
    res = save(client, profile_text(key="second-variant"), sample_document(client))
    assert res.status_code == 201, res.text
    body = res.json()
    assert (body["status"], body["source"], body["proposed_by"]) == (
        "proposed",
        "pasted",
        "login:1",
    )
    assert body["activated_at"] is None and body["ended_at"] is None
    assert registry.saved_profile("second-variant") is None
    with sessions() as session:
        row = session.get(DocumentProfile, body["id"])
        assert row is not None and row.proposed_token_id is None and row.ai_log_id is None
        event = session.scalars(
            select(AuditEvent).where(
                AuditEvent.entity_type == "document_profile", AuditEvent.entity_id == row.id
            )
        ).one()
        assert (event.action, event.actor_kind, event.actor_ref) == ("create", "owner", "login:1")


def test_the_same_key_and_version_cannot_be_saved_twice(client: TestClient) -> None:
    res = save(client, profile_text(key=VARIANT_KEY), sample_document(client))
    assert res.status_code == 422
    assert "raise the version" in res.json()["errors"][0]["message"]
    assert (
        save(client, profile_text(key=VARIANT_KEY, version=2), sample_document(client)).status_code
        == 201
    )


# --- the snapshot holds no figure --------------------------------------------------------------


def test_a_test_snapshot_holds_no_value_and_no_document_text(
    client: TestClient, sessions: Any
) -> None:
    secret = "7391846.27"
    text = (
        "Invented statement heading\n"
        "Total gross amount        $7,391,846.27\n"
        "Leave payment type        Annual leave paid out\n"
    )
    doc = add_document(client, text.encode(), "text/plain", "planted.txt")
    draft = json.dumps(
        {
            "key": "planted-values",
            "version": 1,
            "title": "Planted values",
            "target": "tax.income_statement",
            "claim": ["Invented statement heading"],
            "fields": [
                {
                    "key": "gross_payments",
                    "type": "money",
                    "labels": ["Total gross amount"],
                    "pair": "same_row",
                    "required": True,
                },
                {
                    "key": "leave_payment_type",
                    "type": "text",
                    "labels": ["Leave payment type"],
                    "pair": "same_row",
                },
            ],
        }
    )
    tested = client.post(f"{URL}/test", json={"profile_json": draft, "document_id": doc})
    assert tested.status_code == 200, tested.text
    # The live Reading does carry the planted value (so the planting is real) ...
    values = {f["key"]: f["value"] for f in tested.json()["reading"]["found"]}
    assert (
        values["gross_payments"] == secret
        and values["leave_payment_type"] == "Annual leave paid out"
    )
    saved = save(client, draft, doc)
    assert saved.status_code == 201, saved.text
    # ... and nothing saved does: not the snapshot, not the row, not the audit trail.
    with sessions() as session:
        row = session.get(DocumentProfile, saved.json()["id"])
        assert row is not None
        stored = json.dumps(row.test_snapshot)
        audits = " ".join(
            f"{e.summary} {e.before} {e.after}" for e in session.scalars(select(AuditEvent)).all()
        )
    for place in (stored, json.dumps(saved.json()["test_snapshot"]), audits):
        for planted in (secret, "7,391,846", "7391846", "Annual leave", "statement heading"):
            assert planted not in place
    assert saved.json()["test_snapshot"]["outcome"] in ("ok", "warnings")
    states = {f["key"]: f["state"] for f in saved.json()["test_snapshot"]["fields"]}
    assert states["gross_payments"] == "found"


def test_snapshot_of_keeps_states_and_checks_but_never_a_value() -> None:
    reading = Reading(
        "warnings",
        "p",
        1,
        (Found("gross_payments", Money(739184627, "AUD"), 2, "Total gross amount"),),
        (
            NotFound("tax_withheld", "ambiguous", True),
            NotFound("net_pay", "label_not_found", False),
        ),
        (),
    )
    snap = store.snapshot_of(reading)
    assert snap == {
        "outcome": "warnings",
        "fields": [
            {"key": "gross_payments", "state": "found", "page": 2},
            {"key": "tax_withheld", "state": "ambiguous", "reason": "ambiguous", "required": True},
            {
                "key": "net_pay",
                "state": "not_found",
                "reason": "label_not_found",
                "required": False,
            },
        ],
        "checks": [],
    }
    assert "739184627" not in json.dumps(snap) and "Total gross" not in json.dumps(snap)


# --- lifecycle, override and restore -----------------------------------------------------------


def test_activate_registers_without_a_restart_and_retire_unregisters(
    client: TestClient, sessions: Any
) -> None:
    pid = variant_id(client)
    res = client.post(f"{URL}/{pid}/activate")
    assert res.status_code == 200, res.text
    assert res.json()["profile"]["status"] == "active"
    # The shipped profile also claims the sample statement: a warning, not a refusal.
    assert res.json()["also_claimed_by"] == [SHIPPED_KEY]
    saved = registry.saved_profile(VARIANT_KEY)
    assert saved is not None and registry.shipped_profile(VARIANT_KEY) is None
    assert VARIANT_KEY in {p.key for p in registry.for_target("tax.income_statement")}

    retired = client.post(f"{URL}/{pid}/retire")
    assert retired.status_code == 200, retired.text
    assert retired.json()["status"] == "retired" and retired.json()["ended_reason"] == "retired"
    assert registry.saved_profile(VARIANT_KEY) is None
    assert VARIANT_KEY not in {p.key for p in registry.for_target("tax.income_statement")}
    with sessions() as session:
        actions = [
            e.action
            for e in session.scalars(
                select(AuditEvent)
                .where(AuditEvent.entity_type == "document_profile", AuditEvent.entity_id == pid)
                .order_by(AuditEvent.id)
            )
        ]
    assert actions == ["create", "update", "close"]
    # A retired profile cannot be activated or retired again.
    assert client.post(f"{URL}/{pid}/activate").status_code == 422
    assert client.post(f"{URL}/{pid}/retire").status_code == 422


def test_a_saved_profile_with_a_shipped_key_overrides_it_and_retiring_restores_it(
    client: TestClient,
) -> None:
    doc = sample_document(client)
    created = save(client, profile_text(key=SHIPPED_KEY, version=SHIPPED_VERSION + 1), doc)
    assert created.status_code == 201, created.text
    assert reading_profile(client) == (
        SHIPPED_KEY,
        SHIPPED_VERSION,
    )  # proposed: the shipped one reads
    pid = created.json()["id"]
    assert client.post(f"{URL}/{pid}/activate").status_code == 200
    assert reading_profile(client) == (SHIPPED_KEY, SHIPPED_VERSION + 1)  # the saved one overrides
    listing = summaries(client)
    assert listing[f"{SHIPPED_KEY}@{SHIPPED_VERSION + 1}"]["overrides_shipped"] is True
    assert listing[f"{SHIPPED_KEY}@{SHIPPED_VERSION}"]["overridden"] is True
    assert client.post(f"{URL}/{pid}/retire").status_code == 200
    assert reading_profile(client) == (SHIPPED_KEY, SHIPPED_VERSION)  # restored
    assert summaries(client)[f"{SHIPPED_KEY}@{SHIPPED_VERSION}"]["overridden"] is False


def test_activating_a_new_version_replaces_the_previous_active_one(
    client: TestClient, sessions: Any
) -> None:
    doc = sample_document(client)
    first = variant_id(client)
    second = save(client, profile_text(key=VARIANT_KEY, version=2), doc).json()["id"]
    assert client.post(f"{URL}/{first}/activate").status_code == 200
    assert client.post(f"{URL}/{second}/activate").status_code == 200
    found = registry.saved_profile(VARIANT_KEY)
    assert found is not None and found.version == 2
    old = client.get(f"{URL}/{first}").json()
    assert (old["status"], old["ended_reason"]) == ("retired", "replaced")
    assert client.get(f"{URL}/{second}").json()["status"] == "active"
    with sessions() as session:
        assert (
            session.scalar(
                select(func.count()).where(
                    DocumentProfile.key == VARIANT_KEY,
                    DocumentProfile.ended_at.is_(None),
                    DocumentProfile.activated_at.is_not(None),
                )
            )
            == 1
        )


def test_a_different_key_adds_a_profile(client: TestClient) -> None:
    pid = variant_id(client)
    assert client.post(f"{URL}/{pid}/activate").status_code == 200
    keys = {p.key for p in registry.for_target("tax.income_statement")}
    assert keys == {SHIPPED_KEY, VARIANT_KEY}
    assert registry.shipped_profile(SHIPPED_KEY) is not None


def test_retiring_a_proposed_profile_leaves_the_registry_alone(client: TestClient) -> None:
    pid = variant_id(client)
    res = client.post(f"{URL}/{pid}/retire")
    assert res.status_code == 200 and res.json()["status"] == "retired"
    assert registry.saved_profile(VARIANT_KEY) is None
    assert reading_profile(client) == (SHIPPED_KEY, SHIPPED_VERSION)


# --- the activate guard ------------------------------------------------------------------------


def test_activation_is_refused_when_the_profile_no_longer_reads_its_test_document(
    client: TestClient, sessions: Any
) -> None:
    """A proposed row whose profile cannot read the document it was tested on (here: a claim
    phrase the document does not have) is refused, and nothing changes."""
    doc = sample_document(client)
    other = json.loads(profile_text(key="stale-claim"))
    other["claim"] = ["A phrase the statement never contains"]
    with sessions.begin() as session:
        sha = client.get(f"/api/library/documents/{doc}").json()["sha256"]
        session.add(
            DocumentProfile(
                key="stale-claim",
                version=1,
                target="tax.income_statement",
                profile_json=other,
                source="pasted",
                proposed_by="login:1",
                created_at=store._now(),
                test_document_id=doc,
                test_document_sha256=sha,
                test_snapshot={"outcome": "ok", "fields": [], "checks": []},
            )
        )
    stale = next(p for p in client.get(URL).json() if p["key"] == "stale-claim")
    res = client.post(f"{URL}/{stale['id']}/activate")
    assert res.status_code == 422
    assert res.json()["errors"][0]["field"] == "profile"
    assert client.get(f"{URL}/{stale['id']}").json()["status"] == "proposed"
    assert registry.saved_profile("stale-claim") is None
    review = client.get(f"{URL}/{stale['id']}/review").json()
    assert review["acceptable"] is False and review["reading"]["outcome"] == "unrecognised"


def test_activation_is_refused_when_the_test_document_has_left_the_library(
    client: TestClient,
) -> None:
    pid = variant_id(client)
    doc = sample_document(client)
    assert client.delete(f"/api/library/documents/{doc}").status_code == 204
    res = client.post(f"{URL}/{pid}/activate")
    assert res.status_code == 422
    assert res.json()["errors"][0]["field"] == "document"
    assert client.get(f"{URL}/{pid}").json()["test_document"] is None
    assert client.get(f"{URL}/{pid}").json()["status"] == "proposed"


def test_review_reruns_the_profile_live_beside_its_document(client: TestClient) -> None:
    pid = variant_id(client)
    res = client.get(f"{URL}/{pid}/review")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["acceptable"] is True and body["reading"]["outcome"] == "ok"
    assert {f["key"]: f["value"] for f in body["reading"]["found"]}["gross_payments"] == "84500.00"


def test_only_an_owner_saves_activates_or_retires(settings: Settings) -> None:
    app = create_app(settings)
    viewer = Principal(login_id=2, role=Role.VIEWER)
    with owner_client(app) as owner:
        pid = variant_id(owner)
        doc = sample_document(owner)
        as_owner(app, viewer)
        assert owner.get(URL).status_code == 200  # a viewer may look
        assert owner.post(f"{URL}/{pid}/activate").status_code == 403
        assert owner.post(f"{URL}/{pid}/retire").status_code == 403
        assert save(owner, profile_text(key="viewer-try"), doc).status_code == 403
        assert owner.get(f"{URL}/{pid}").json()["status"] == "proposed"


def test_an_unknown_profile_is_not_found(client: TestClient) -> None:
    assert client.get(f"{URL}/9999").status_code == 404
    assert client.post(f"{URL}/9999/activate").status_code == 404
    assert client.post(f"{URL}/9999/retire").status_code == 404


# --- append-only in the database itself --------------------------------------------------------


def test_the_table_is_append_only_and_lifecycle_columns_are_set_once(
    client: TestClient, settings: Settings
) -> None:
    pid = variant_id(client)
    assert client.post(f"{URL}/{pid}/activate").status_code == 200
    conn = sqlite3.connect(db_path(settings.data_dir))
    try:

        def refused(sql: str) -> None:
            with pytest.raises(sqlite3.DatabaseError, match="append-only"):
                conn.execute(sql, (pid,))

        refused("DELETE FROM document_profile WHERE id = ?")
        refused("UPDATE document_profile SET profile_json = '{}' WHERE id = ?")
        refused("UPDATE document_profile SET key = 'other' WHERE id = ?")
        refused("UPDATE document_profile SET test_snapshot = '{}' WHERE id = ?")
        refused("UPDATE document_profile SET activated_at = '2000-01-01 00:00:00' WHERE id = ?")
        refused("UPDATE document_profile SET activated_by = 'someone' WHERE id = ?")
        # An active row cannot go back to proposed.
        refused("UPDATE document_profile SET activated_at = NULL, activated_by = NULL WHERE id = ?")
        # Ending is allowed once; after that nothing changes.
        conn.execute(
            "UPDATE document_profile SET ended_at = '2026-11-08 00:00:00', ended_by = 'x', "
            "ended_reason = 'retired' WHERE id = ?",
            (pid,),
        )
        refused("UPDATE document_profile SET ended_at = '2027-01-01 00:00:00' WHERE id = ?")
        refused("UPDATE document_profile SET ended_reason = 'replaced' WHERE id = ?")
        refused(
            "UPDATE document_profile SET ended_at = NULL, ended_by = NULL, "
            "ended_reason = NULL WHERE id = ?"
        )
        # A retired row cannot be activated afterwards.
        refused("UPDATE document_profile SET activated_at = '2000-01-01 00:00:00' WHERE id = ?")
    finally:
        conn.rollback()
        conn.close()


def test_at_most_one_row_is_active_per_key(client: TestClient, settings: Settings) -> None:
    doc = sample_document(client)
    first = variant_id(client)
    second = save(client, profile_text(key=VARIANT_KEY, version=2), doc).json()["id"]
    assert client.post(f"{URL}/{first}/activate").status_code == 200
    conn = sqlite3.connect(db_path(settings.data_dir))
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE document_profile SET activated_at = '2026-11-08 00:00:00', "
                "activated_by = 'x' WHERE id = ?",
                (second,),
            )
    finally:
        conn.close()


# --- start-up containment and reload -----------------------------------------------------------


def plant(
    session: Session,
    key: str,
    profile: Any,
    *,
    target: str = "tax.income_statement",
    version: int = 1,
) -> int:
    """An *active* row written straight to the table, bypassing every service check."""
    row = DocumentProfile(
        key=key,
        version=version,
        target=target,
        profile_json=profile,
        source="pasted",
        proposed_by="login:1",
        created_at=store._now(),
        test_document_id=1,
        test_document_sha256="0" * 64,
        test_snapshot={"outcome": "ok", "fields": [], "checks": []},
        activated_at=store._now(),
        activated_by="login:1",
    )
    session.add(row)
    session.flush()
    return row.id


def test_a_bad_saved_row_is_skipped_reported_and_never_stops_start_up(
    settings: Settings, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    migrate(settings.data_dir)
    base = {**json.loads(SHIPPED_JSON), "version": 1}  # the planted rows are version 1
    engine = make_engine(db_path(settings.data_dir))
    with make_session_factory(engine).begin() as session:
        good = plant(session, "good-variant", {**base, "key": "good-variant"})
        bad_target = plant(
            session, "bad-target", {**base, "key": "bad-target", "target": "no.such_form"}
        )
        bad_digits = plant(
            session, "bad-digits", {**base, "key": "bad-digits", "claim": ["Account 7654321 x"]}
        )
        bad_shape = plant(session, "bad-shape", [1, 2, 3])
        bad_match = plant(session, "bad-match", {**base, "key": "some-other-key"})
        explodes = plant(session, "bad-explodes", {**base, "key": "bad-explodes"})
        bad_field = plant(
            session,
            "bad-field",
            {
                **base,
                "key": "bad-field",
                "fields": [{"key": "nope", "type": "money", "labels": ["A"], "pair": "same_row"}],
            },
        )
    engine.dispose()

    # A row that fails in a way nobody anticipated (not a ProfileError) is contained too.
    real = store.profile_of

    def profile_of(row: DocumentProfile) -> Any:
        if row.key == "bad-explodes":
            raise ZeroDivisionError("invented secret text 7654321")
        return real(row)

    monkeypatch.setattr(store, "profile_of", profile_of)
    caplog.set_level(logging.WARNING)
    with owner_client(create_app(settings)) as client:  # start-up completes
        assert client.get("/health").status_code == 200
        listing = {p["id"]: p for p in client.get(URL).json() if p["id"] is not None}
        assert listing[good]["status"] == "active"
        failed = [bad_target, bad_digits, bad_shape, bad_match, bad_field, explodes]
        for row_id in failed:
            assert listing[row_id]["status"] == "failed_to_load", row_id
            assert listing[row_id]["failure"]
        assert "7654321" not in json.dumps(listing[bad_digits])
        assert "unknown target" in listing[bad_target]["failure"]
        assert "7654321" not in json.dumps(listing[explodes])  # the exception text is not shown
        # The good profile registered and the shipped one is untouched.
        assert registry.saved_profile("good-variant") is not None
        for row_id in failed:
            key = listing[row_id]["key"]
            assert registry.saved_profile(key) is None
        assert reading_profile(client) == (SHIPPED_KEY, SHIPPED_VERSION)
        # A failed row can be retired from Settings.
        assert client.post(f"{URL}/{bad_target}/retire").status_code == 200
        assert client.get(f"{URL}/{bad_target}").json()["status"] == "retired"
    # Logged by key and reason only: no label text, and the digit run never reaches the log.
    assert "bad-digits" in caplog.text and "failed to load" in caplog.text
    assert "7654321" not in caplog.text


def test_active_saved_profiles_load_on_the_next_start(settings: Settings) -> None:
    with owner_client(create_app(settings)) as client:
        pid = variant_id(client)
        assert client.post(f"{URL}/{pid}/activate").status_code == 200
    assert registry.saved_profile(VARIANT_KEY) is None  # gone with the process's app
    with owner_client(create_app(settings)) as client:
        assert registry.saved_profile(VARIANT_KEY) is not None
        assert summaries(client)[f"{VARIANT_KEY}@1"]["status"] == "active"
        # The fixture does not save a second copy on the next start.
        assert len([k for k in summaries(client) if k.startswith(VARIANT_KEY)]) == 1


def test_a_retired_profile_stays_off_after_a_restart(settings: Settings) -> None:
    with owner_client(create_app(settings)) as client:
        pid = variant_id(client)
        client.post(f"{URL}/{pid}/activate")
        client.post(f"{URL}/{pid}/retire")
    with owner_client(create_app(settings)) as client:
        assert registry.saved_profile(VARIANT_KEY) is None
        assert summaries(client)[f"{VARIANT_KEY}@1"]["status"] == "retired"


# --- the registry ------------------------------------------------------------------------------


def test_the_registry_overrides_adds_and_restores() -> None:
    from finance360.capabilities.documents.profile import load_profile
    from support import register_income_statement_target

    register_income_statement_target()
    registry.register(PROFILE)
    try:
        override = load_profile({**json.loads(SHIPPED_JSON), "version": 7})
        extra = load_profile({**json.loads(SHIPPED_JSON), "key": "another-one"})
        registry.register_saved(override)
        registry.register_saved(extra)
        assert [p.key for p in registry.for_target("tax.income_statement")] == [
            "another-one",
            SHIPPED_KEY,
        ]
        assert registry.for_target("tax.income_statement")[1].version == 7
        registry.unregister_saved(SHIPPED_KEY)
        registry.unregister_saved("another-one")
        assert registry.for_target("tax.income_statement") == [PROFILE]
        # A saved profile registered before the shipped one is registered still wins.
        registry.register_saved(override)
        registry.register(PROFILE)
        assert registry.for_target("tax.income_statement")[0].version == 7
    finally:
        registry.clear_saved()
        registry.unregister(PROFILE.key)
