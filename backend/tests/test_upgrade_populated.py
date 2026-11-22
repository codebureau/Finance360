"""The populated-database upgrade test (ADR-0013 section 1).

The deployment holds real household data, so every M2 migration must be additive. This builds a
database at the last pre-M2 revision, fills it from the synthetic household, then upgrades one
revision at a time to head. After each step it asserts that every table, column, trigger and index
that existed before is unchanged and that every pre-existing row is identical, and that the audit
triggers still reject UPDATE and DELETE.

**Each M2 migration slice extends this**: add its revision's seeder to ``SEEDERS`` (data for the
tables that revision created, loaded through the real services), so the next step proves those rows
survive too. The steps themselves are discovered from the migration scripts.

Each seeder runs as the schema stood at its revision (``_schema_as_it_stood``): a column a later
migration added to an existing table is left out of the ORM's reads and writes, so the models
carry no accommodation for this test. A table that does not exist yet needs plain SQL.

``test_the_check_fails_when_a_migration_alters_existing_data`` proves the check bites: it appends a
deliberately destructive migration and expects the check to reject it.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, event, inspect
from sqlalchemy.orm import ORMExecuteState, Session, defer

from finance360.capabilities.household.service import bootstrap
from finance360.capabilities.imports import account_identifiers
from finance360.capabilities.position import accounts as position_accounts
from finance360.capabilities.position import valuations as position_valuations
from finance360.capabilities.reference import fx_rates as reference_fx_rates
from finance360.capabilities.reference import instruments as reference_instruments
from finance360.capabilities.reference import prices as reference_prices
from finance360.core import rulepack, rulepack_store
from finance360.core.catalogue import seed_catalogue
from finance360.core.db import Base, db_path, make_engine, make_session_factory
from finance360.core.migrate import MIGRATIONS_DIR, alembic_config
from finance360.plugins.importers.vanguard_pi import IMPORTER

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "fixtures" / "sample_household" / "household.json"

#: The last revision before M2 (ADR-0013): everything up to here is live household data.
BASELINE = "0009"

#: Scenarios the 0018 seeder writes by hand (the fixture's custom one, as it was before phases).
EARLY = "Retire early, spend more"
NOTHING = "Spend nothing (upgrade check)"


# --- seeding -------------------------------------------------------------------------------


def _without_effort_level(path: Path) -> None:
    """The fixture as it stood before 0023 (no Household Effort level), for the baseline seed: the
    column does not exist yet. The 0023 seeder sets it from the real fixture."""
    data = json.loads(path.read_text(encoding="utf-8"))
    data["household"].pop("effort_level", None)
    path.write_text(json.dumps(data), encoding="utf-8")


def _seed_baseline(session: Session, data_dir: Path) -> None:
    seed_catalogue(session)
    bootstrap(session, data_dir)
    position_accounts.load_fixture_accounts(session, data_dir)
    position_valuations.load_fixture_valuations(session, data_dir)
    reference_fx_rates.load_fixture_fx_rates(session, data_dir)


def _seed_instruments(session: Session, data_dir: Path) -> None:
    reference_instruments.load_fixture_instruments(session, data_dir)


def _seed_prices(session: Session, data_dir: Path) -> None:
    reference_prices.load_fixture_prices(session, data_dir)


def _seed_imports(session: Session, data_dir: Path) -> None:
    """Revision 0012 created ``import_source``, ``import`` and ``staged_row``: stage the sample
    trade export. Written with the models, not ``stage()``, because the service also reads
    ``account_identifier`` (created by the next revision, which does not exist yet here)."""
    from datetime import UTC, datetime
    from hashlib import sha256

    from sqlalchemy import select

    from finance360.core.models.account import Account
    from finance360.core.models.imports import Import, ImportSource, StagedRow

    name = "investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv"
    content = (FIXTURE.parent / "imports" / name).read_bytes()
    parsed = IMPORTER.parse(content, {}, name)
    account = session.scalars(
        select(Account).where(Account.name == "Sample Brokerage account")
    ).one()
    now = datetime.now(UTC).replace(tzinfo=None)
    source = ImportSource(
        account_id=account.id, importer_key=IMPORTER.key, config={}, created_at=now
    )
    session.add(source)
    session.flush()
    imp = Import(
        import_source_id=source.id,
        stream=parsed.stream,
        file_sha256=sha256(content).hexdigest(),
        file_name="investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv",
        content=content,
        coverage_from=parsed.coverage_from,
        coverage_to=parsed.coverage_to,
        status="staged",
        warnings=list(parsed.warnings),
        added_at=now,
    )
    session.add(imp)
    session.flush()
    for row in parsed.rows:
        session.add(
            StagedRow(
                import_id=imp.id,
                row_no=row.row_no,
                record=row.to_record(),
                instrument_ref=row.instrument_ref,
                instrument_id=None,
            )
        )


def _seed_account_identifiers(session: Session, data_dir: Path) -> None:
    account_identifiers.load_fixture_account_identifiers(session, data_dir)


def _seed_transactions(session: Session, data_dir: Path) -> None:
    """Revision 0014 created ``transaction``: stage the sample cash export and commit it, through
    the real services, so the rows and the commit's audit event exist before the next step."""
    from sqlalchemy import select

    from finance360.capabilities.imports import commit, service
    from finance360.capabilities.imports import registry as importers
    from finance360.core.audit import AuditContext
    from finance360.core.models.account import Account

    importers.register(IMPORTER)
    account = session.scalars(
        select(Account).where(Account.name == "Sample Brokerage account")
    ).one()
    content = (
        FIXTURE.parent
        / "imports"
        / "cash_transactions_accSAMPLE-PI-0001_01-Jul-2026_30-Sep-2026.csv"
    ).read_bytes()
    context = AuditContext("owner", "login:1", "fixture")
    staged = service.stage(
        session,
        context,
        content,
        "cash_transactions_accSAMPLE-PI-0001_01-Jul-2026_30-Sep-2026.csv",
        account.id,
    )
    commit.commit(session, context, staged.import_.id)


def _seed_fetch_runs(session: Session, data_dir: Path) -> None:
    """Revision 0015 created ``fetch_run``: one finished run with a per-Instrument result."""
    from datetime import UTC, datetime

    from finance360.capabilities.reference import fetch

    run_id = fetch.claim_in(session, "prices", "yfinance", "manual")
    run = fetch.get_run(session, run_id)
    assert run is not None
    run.status = "partial"
    run.finished_at = datetime.now(UTC).replace(tzinfo=None)
    run.added = 3
    run.error = "1 of 2 Instruments failed. Sample Mining Ltd: The provider returned an error."
    run.detail = [
        {"target": "Sample Broad Market ETF", "symbol": "SBME.AX", "outcome": "ok", "added": 3},
        {"target": "Sample Mining Ltd", "symbol": "SMPM.AX", "outcome": "error", "added": 0},
    ]


def _seed_scenarios(session: Session, data_dir: Path) -> None:
    """Revision 0016 created ``scenario`` and ``assumption``: one Scenario with one override.

    It has a key, so the fixture loader (which waits for there to be no custom Scenario) still
    runs later.

    Plain SQL, not the services: the ORM model has since gained the column revision 0018 adds, so
    it cannot read or write this older table shape. The services seed the later revisions."""
    from sqlalchemy import text

    session.execute(
        text(
            "INSERT INTO scenario (key, name, notes, created_at) "
            "VALUES ('upgrade_check', 'Upgrade check scenario', 'Invented.', '2026-10-01 00:00:00')"
        )
    )
    session.execute(
        text(
            "INSERT INTO assumption (scenario_id, key, scope_type, scope_ref, value_int) "
            "SELECT id, 'horizon_age', 'household', '', 90 FROM scenario "
            "WHERE name = 'Upgrade check scenario'"
        )
    )


def _seed_projection_runs(session: Session, data_dir: Path) -> None:
    """Revision 0017 created ``projection_run``: one saved run of that Scenario (plain SQL, as
    above; the stored JSON is a placeholder because nothing reads it)."""
    from sqlalchemy import text

    session.execute(
        text(
            "INSERT INTO projection_run (created_at, label, scenario_id, scenario_name, "
            "engine_version, input_version, input_json, result_json, provenance_json, "
            "funds_last_to_age, lasts_beyond_horizon) "
            "SELECT '2026-10-02 00:00:00', 'Upgrade check', id, name, 1, 1, '{}', '{}', '{}', "
            "NULL, 1 FROM scenario WHERE name = 'Upgrade check scenario'"
        )
    )


def _seed_drawdown_order(session: Session, data_dir: Path) -> None:
    """Revision 0018 added ``scenario.drawdown_order``: the pre-0018 Scenario must still have a
    null (default) order. The Best / Likely / Worst Scenarios are seeded by the service, and the
    household's own Assumptions are written as the live database holds them at this point: **with
    an ``annual_spending`` row in every layer** (the Household default, one Scenario's override and
    a Scenario that spends nothing). Plain SQL for those, because the services have since moved
    spending to ``spending_phase``, which does not exist until the next revision."""
    from sqlalchemy import select, text

    from finance360.capabilities.projection.seed import seed_assumptions
    from finance360.core.models.scenario import Scenario

    old = session.scalars(select(Scenario).where(Scenario.name == "Upgrade check scenario")).one()
    assert old.drawdown_order is None
    seed_assumptions(session)
    member = {
        name: int(session.scalar(text("SELECT id FROM member WHERE name = :n"), {"n": name}) or 0)
        for name in ("Alex Sample", "Jordan Sample")
    }
    assert all(member.values())

    columns = {
        "value_minor": "value_minor, value_ccy",
        "value_int": "value_int",
        "value_rate_ppm": "value_rate_ppm",
    }
    values = {
        "value_minor": ":v, 'AUD'",
        "value_int": ":v",
        "value_rate_ppm": ":v",
    }

    def assume(scenario: str | None, key: str, scope: tuple[str, str], **value: int) -> None:
        ((column, given),) = value.items()
        session.execute(
            text(
                "INSERT INTO assumption "  # noqa: S608 - fixed column names, bound values
                f"(scenario_id, key, scope_type, scope_ref, {columns[column]}) "
                "SELECT (SELECT id FROM scenario WHERE name = :s), :k, :t, :r, " + values[column]
            ),
            {"s": scenario, "k": key, "t": scope[0], "r": scope[1], "v": given},
        )

    household_scope = ("household", "")
    assume(None, "annual_spending", household_scope, value_minor=7_000_000)
    assume(None, "annual_saving", household_scope, value_minor=2_000_000)
    assume(None, "retirement_age", ("member", str(member["Alex Sample"])), value_int=60)
    assume(None, "retirement_age", ("member", str(member["Jordan Sample"])), value_int=62)
    for name, notes, order in (
        (EARLY, "Invented.", '["investments", "cash", "superannuation", "overseas_pensions"]'),
        (NOTHING, "Invented: states that no money is spent.", None),
    ):
        session.execute(
            text(
                "INSERT INTO scenario (key, name, notes, created_at, drawdown_order) "
                "VALUES (NULL, :n, :notes, '2026-10-03 00:00:00', :o)"
            ),
            {"n": name, "notes": notes, "o": order},
        )
    assume(EARLY, "retirement_age", ("member", str(member["Alex Sample"])), value_int=55)
    assume(EARLY, "annual_spending", household_scope, value_minor=8_500_000)
    assume(EARLY, "return_rate", ("category", "investments"), value_rate_ppm=65_000)
    assume(NOTHING, "annual_spending", household_scope, value_minor=0)


def _seed_spending_phases(session: Session, data_dir: Path) -> None:
    """Revision 0019 created ``spending_phase`` and converted ``annual_spending``. Nothing to add:
    the services have since gained a read of the income stream tables (0020), which do not exist
    at this revision, so the run made from phases is saved by the 0020 seeder instead."""


def _seed_income_streams(session: Session, data_dir: Path) -> None:
    """Revision 0020 created ``income_stream`` and ``income_stream_owner``: a salary for each
    Member, through the real service (the fixture's, so they carry its invented amounts)."""
    from sqlalchemy import select

    from finance360.capabilities.projection import income_streams, runs, scenarios
    from finance360.core.models.scenario import Scenario

    # First a run of Likely, through the real services, made from phases and before any salary
    # exists, so a run sits beside the old rows as the 0019 step intended.
    likely = session.scalars(select(Scenario).where(Scenario.key == "likely")).one()
    runs.save_run(session, scenarios.FIXTURE_CONTEXT, likely.id, "Upgrade check phases")
    income_streams.load_fixture_income_streams(session, data_dir)


def _seed_tax_records(session: Session, data_dir: Path) -> None:
    """Revision 0021 created ``tax_record``: the fixture's Tax records, through the real service
    (invented, FY 2025-26, for both Members)."""
    from finance360.capabilities.tax import service as tax_service

    tax_service.load_fixture_tax_records(session, data_dir)


def _seed_tax_evidence(session: Session, data_dir: Path) -> None:
    """Revision 0022 created ``tax_evidence`` and ``tax_record_evidence``: the fixture's one
    invented receipt, attached as the release that had those tables stored it. Plain SQL and a file
    under ``documents/tax/`` by its hash (the service for it is gone: the Library replaced it at
    0028), so the migration meets the rows **and the file** of a real previous release."""
    import hashlib

    from sqlalchemy import text

    shutil.copytree(FIXTURE.parent / "evidence", data_dir / "evidence", dirs_exist_ok=True)
    entries = json.loads(FIXTURE.read_text(encoding="utf-8"))["tax_evidence"]
    assert len(entries) == 1
    for e in entries:
        content = (data_dir / e["file"]).read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        stored = data_dir / "documents" / "tax" / digest[:2] / digest
        stored.parent.mkdir(parents=True, exist_ok=True)
        stored.write_bytes(content)
        record_id = session.scalar(
            text(
                "SELECT r.id FROM tax_record r JOIN member m ON m.id = r.member_id "
                "WHERE m.name = :member AND r.description = :record"
            ),
            {"member": e["member"], "record": e["record"]},
        )
        assert record_id is not None
        session.execute(
            text(
                "INSERT INTO tax_evidence (sha256, file_name, media_type, size_bytes, added_at) "
                "VALUES (:sha, :name, :type, :size, '2026-11-03 00:00:00')"
            ),
            {
                "sha": digest,
                "name": Path(e["file"]).name,
                "type": e["media_type"],
                "size": len(content),
            },
        )
        session.execute(
            text(
                "INSERT INTO tax_record_evidence (record_id, evidence_id, added_at) "
                "SELECT :record, id, '2026-11-03 00:00:00' FROM tax_evidence WHERE sha256 = :sha"
            ),
            {"record": record_id, "sha": digest},
        )


def _seed_whats_due(session: Session, data_dir: Path) -> None:
    """Revision 0023 added ``household.effort_level`` and ``due_snooze``: set the fixture's Effort
    level through the real Household service and snooze one item through the real Due service."""
    from finance360.capabilities.due import service as due_service
    from finance360.capabilities.household import service as household_service
    from finance360.main import register_due_rules

    register_due_rules()
    level = json.loads(FIXTURE.read_text(encoding="utf-8"))["household"]["effort_level"]
    household_service.set_effort_level(session, household_service.FIXTURE_CONTEXT, level)
    due_service.snooze(
        session,
        household_service.FIXTURE_CONTEXT,
        "position.slow_valuation",
        "account:1@2026-01-01",
        "month",
        today=date(2026, 11, 3),
    )


def _seed_rule_capture(session: Session, data_dir: Path) -> None:
    """Revision 0024 created ``rule_capture``: capture one invented FY 2026-27 car rate through the
    real store (the release holds none for that year), so the step proves the row survives."""
    from finance360.capabilities.household import service as household_service
    from finance360.core import rulepack_store

    rulepack_store.capture_figure(
        session,
        household_service.FIXTURE_CONTEXT,
        "au",
        "tax.car.rate_per_km",
        2027,
        rulepack_store.CaptureInput("0.90", quote="An invented sentence about the rate."),
        today=date(2026, 11, 5),
    )


def _seed_ai_log(session: Session, data_dir: Path) -> None:
    """Revision 0025 created ``ai_log``: make one invented Ask call through the real gateway (a
    scripted provider, redacting this household's names), so the step proves the row survives."""
    from ai_support import FakeProvider, cited, settings

    from finance360.capabilities.ai import gateway, providers

    fake = FakeProvider(cited("Start with the Guide."))
    providers.register(fake)
    try:
        gateway.complete(
            session,
            settings(fake.key),
            gateway.AiRequest(
                "ask",
                "Ask panel question",
                gateway.GUIDANCE,
                [gateway.Part("conversation", "Where do I begin?", role="user")],
                600,
            ),
        )
    finally:
        providers.unregister(fake.key)


def _seed_matched_capture(session: Session, data_dir: Path) -> None:
    """Revision 0026 widened ``rule_capture.method`` to allow ``matched`` (a table rebuild): capture
    one invented figure the word matcher "found", through the real store. The insert fails
    unless the wider CHECK is in the rebuilt table, and the step proves the 0024 row survived."""
    from finance360.capabilities.household import service as household_service
    from finance360.core import rulepack_store

    proposal: dict[str, object] = {
        "key": "tax.car.max_km",
        "fy": 2027,
        "value": "5000",
        "quote": "An invented sentence about the limit.",
        "provisional": False,
        "note": "Matched by words on the page.",
    }
    rulepack_store.capture_figure(
        session,
        household_service.FIXTURE_CONTEXT,
        "au",
        "tax.car.max_km",
        2027,
        rulepack_store.CaptureInput(
            "5000",
            quote="An invented sentence about the limit.",
            ai=rulepack_store.AiProvenance(None, proposal, "matched"),
        ),
        today=date(2026, 11, 7),
    )


def _seed_carried_capture(session: Session, data_dir: Path) -> None:
    """Revision 0030 widened ``rule_capture.method`` to allow ``carried`` (another table rebuild):
    write one invented provisional carried figure through the real store. The insert fails unless
    the wider CHECK is in the rebuilt table, and the step proves the earlier rows survived."""
    from finance360.capabilities.household import service as household_service
    from finance360.core import rulepack_store

    rulepack_store.capture_figure(
        session,
        household_service.FIXTURE_CONTEXT,
        "au",
        "tax.home_office.rate_per_hour",
        2027,
        rulepack_store.CaptureInput(
            "0.70",
            provisional_note="Same as 2025-26, not checked against the page",
            carried=True,
        ),
        today=date(2026, 11, 14),
    )


def _seed_super_statements(session: Session, data_dir: Path) -> None:
    """Revision 0027 created ``super_statement`` and ``super_statement_option``: the fixture's two
    invented Super statements (one with three Investment options), through the real service, each
    recording its 30 June Valuation beside the Valuations that were already there."""
    from finance360.capabilities.position import super_statements

    assert super_statements.load_fixture_super_statements(session, data_dir) == 2


def _seed_library(session: Session, data_dir: Path) -> None:
    """Revision 0028 created ``library_document`` and ``document_link`` from the Evidence tables:
    add one more invented document through the real Library service and link it to a second Tax
    record, so the step proves the new tables take writes beside the converted rows."""
    from sqlalchemy import select

    from finance360.capabilities.household import service as household_service
    from finance360.capabilities.library import registry as link_kinds
    from finance360.capabilities.library import service as library
    from finance360.capabilities.tax import link_kind
    from finance360.core.models.tax_record import TaxRecord

    link_kinds.register(link_kind.KIND)
    added = library.add_document(
        session,
        household_service.FIXTURE_CONTEXT,
        data_dir,
        file_name="upgrade_check_note.txt",
        media_type="text/plain",
        content=b"An invented note for the upgrade check.\n",
    )
    assert added.created and added.document.text_status == "extracted"
    spare = session.scalars(select(TaxRecord).order_by(TaxRecord.id.desc())).first()
    assert spare is not None
    library.link(
        session, household_service.FIXTURE_CONTEXT, added.document.id, "tax_record", spare.id
    )


def _seed_document_profile(session: Session, data_dir: Path) -> None:
    """Revision 0029 created ``document_profile``: save one invented Proposed profile through the
    real store, tested on a Library document the step adds, so the table takes writes (and its
    triggers fire) on a populated database."""
    from finance360.capabilities.documents import registry as document_registry
    from finance360.capabilities.documents import store
    from finance360.capabilities.documents.reading import OK, Reading
    from finance360.capabilities.household import service as household_service
    from support import register_income_statement_target

    register_income_statement_target()
    profile = store.validate_json(
        {
            "key": "upgrade-check",
            "version": 1,
            "title": "Upgrade check",
            "target": "tax.income_statement",
            "claim": ["Invented heading"],
            "fields": [
                {"key": "gross_payments", "type": "money", "labels": ["Total"], "pair": "same_row"}
            ],
        }
    )
    assert document_registry.get_target(profile.target) is not None
    store.propose(
        session,
        household_service.FIXTURE_CONTEXT,
        profile=profile,
        raw={"key": "upgrade-check", "version": 1},
        source="pasted",
        document_id=1,
        document_sha256="0" * 64,
        reading=Reading(OK, "upgrade-check", 1, (), (), ()),
    )


def _seed_tax_estimates(session: Session, data_dir: Path) -> None:
    """Revision 0031 created ``tax_whatif`` and ``tax_estimate``: the fixture's two Tax what-ifs and
    its saved base estimate, through the real services (invented, FY 2025-26, the Tax records the
    0021 seeder wrote)."""
    from finance360.capabilities.tax import saved as tax_saved

    shutil.copy(FIXTURE.parent / tax_saved.FIXTURE_FILENAME, data_dir / tax_saved.FIXTURE_FILENAME)
    assert tax_saved.load_fixture_tax_whatifs(session, data_dir) == 3


def _seed_tax_lodged_results(session: Session, data_dir: Path) -> None:
    """Revision 0032 created ``tax_lodged_result``: the fixture's Jordan notice and its amendment,
    through the real service (invented, FY 2025-26)."""
    from finance360.capabilities.tax import lodged

    shutil.copy(FIXTURE.parent / lodged.FIXTURE_FILENAME, data_dir / lodged.FIXTURE_FILENAME)
    assert lodged.load_fixture_lodged_results(session, data_dir) == 2


def _seed_household_facts(session: Session, data_dir: Path) -> None:
    """Revision 0033 added three nullable Household fact columns: set the fixture's principal home
    through the real service (the Accounts are already there from the baseline seeder)."""
    from finance360.capabilities.household import service as household_service

    household_service.load_fixture_facts(session, data_dir)


#: revision -> loads the data its migration created tables for. Extend with each schema slice.
SEEDERS: dict[str, Callable[[Session, Path], None]] = {
    BASELINE: _seed_baseline,
    "0010": _seed_instruments,
    "0011": _seed_prices,
    "0012": _seed_imports,
    "0013": _seed_account_identifiers,
    "0014": _seed_transactions,
    "0015": _seed_fetch_runs,
    "0016": _seed_scenarios,
    "0017": _seed_projection_runs,
    "0018": _seed_drawdown_order,
    "0019": _seed_spending_phases,
    "0020": _seed_income_streams,
    "0021": _seed_tax_records,
    "0022": _seed_tax_evidence,
    "0023": _seed_whats_due,
    "0024": _seed_rule_capture,
    "0025": _seed_ai_log,
    "0026": _seed_matched_capture,
    "0027": _seed_super_statements,
    "0028": _seed_library,
    "0029": _seed_document_profile,
    "0030": _seed_carried_capture,
    "0031": _seed_tax_estimates,
    "0032": _seed_tax_lodged_results,
    "0033": _seed_household_facts,
}


@contextmanager
def _schema_as_it_stood(engine: Engine) -> Iterator[None]:
    """Let today's models and services read and write a database from an older revision.

    A column a later migration added to a table that already existed is in the model but not in the
    database yet, so the ORM's SELECT (and the RETURNING after an INSERT) would name a column that
    is not there. The database is reflected, and for each mapped column it lacks: SELECTs defer it,
    and INSERTs do not fetch it back. Nothing is hard-coded per migration, and the models carry no
    accommodation for this test. (A whole table that does not exist yet is not covered: the seeder
    for that revision writes plain SQL instead, as ``_seed_scenarios`` does.)
    """
    have = inspect(engine)
    absent: dict[type, list[str]] = {}
    missing_columns: dict[str, set[str]] = {}  # table -> columns the old database lacks
    for mapper in Base.registry.mappers:
        table = str(getattr(mapper.local_table, "name", ""))
        if not have.has_table(table):
            continue
        present = {c["name"] for c in have.get_columns(table)}
        keys = [p.key for p in mapper.column_attrs if not {c.name for c in p.columns} <= present]
        if keys:
            absent[mapper.class_] = keys
            missing_columns[table] = {
                c.name for c in mapper.local_table.columns if c.name not in present
            }

    def drop_absent_from_insert(
        _conn: object, _cursor: object, statement: str, parameters: Any, _ctx: object, many: bool
    ) -> tuple[str, Any]:
        """A nullable column with no default is written as NULL by every ORM INSERT. On a database
        that does not have it yet, leave it out (its value is always NULL there)."""
        found = re.match(
            r"INSERT INTO (\w+) \(([^)]*)\) VALUES (.*?)( RETURNING .*)?$", statement, re.S
        )
        if not found or found.group(1) not in missing_columns:
            return statement, parameters
        table, names, values, returning = found.groups()
        cols = [c.strip() for c in names.split(",")]
        keep = [i for i, c in enumerate(cols) if c not in missing_columns[table]]
        if len(keep) == len(cols):
            return statement, parameters
        groups = re.findall(r"\(([^()]*)\)", values)
        assert all(g.split(", ").count("?") == len(cols) for g in groups), statement

        def trim(row: tuple[Any, ...]) -> tuple[Any, ...]:
            assert all(row[i] is None for i in range(len(cols)) if i not in keep)
            return tuple(row[i] for i in keep)

        rows = (
            parameters
            if many
            else [
                tuple(parameters[r * len(cols) : (r + 1) * len(cols)]) for r in range(len(groups))
            ]
        )
        trimmed = [trim(tuple(r)) for r in rows]
        new_values = ", ".join(
            "(" + ", ".join(g.split(", ")[i] for i in keep) + ")" for g in groups
        )
        text = f"INSERT INTO {table} ({', '.join(cols[i] for i in keep)}) VALUES {new_values}"  # noqa: S608
        flat = trimmed if many else tuple(v for r in trimmed for v in r)
        return text + (returning or ""), flat

    def defer_absent(state: ORMExecuteState) -> object:
        if not state.is_select or state.is_column_load or state.is_relationship_load:
            return None
        # Only a SELECT of whole entities loads absent columns; select(Household.name) does not.
        selected = {
            d["entity"]
            for d in state.statement.column_descriptions  # type: ignore[union-attr]
            if d["expr"] is d["entity"]
        }
        options = [
            defer(getattr(mapper.class_, key))
            for mapper in state.all_mappers
            if mapper.class_ in selected
            for key in absent.get(mapper.class_, ())
        ]
        if not options:
            return None
        return state.invoke_statement(statement=state.statement.options(*options))

    mappers = {m.class_: m for m in Base.registry.mappers}
    saved = {cls: mappers[cls].eager_defaults for cls in absent}
    # A database from before 0024 has no ``rule_capture`` table, so it has no captures: services
    # that read the effective pack (a projection, a Tax record) see exactly the registered pack,
    # which is what the merge returns for an empty table.
    no_captures = not have.has_table("rule_capture")
    real_effective_pack = rulepack_store.effective_pack
    if no_captures:
        rulepack_store.effective_pack = lambda _session, jurisdiction: rulepack.get(jurisdiction)
    event.listen(Session, "do_orm_execute", defer_absent)
    event.listen(engine, "before_cursor_execute", drop_absent_from_insert, retval=True)
    for cls in absent:
        mappers[cls].eager_defaults = False
    try:
        yield
    finally:
        rulepack_store.effective_pack = real_effective_pack
        event.remove(Session, "do_orm_execute", defer_absent)
        event.remove(engine, "before_cursor_execute", drop_absent_from_insert)
        for cls, value in saved.items():
            mappers[cls].eager_defaults = value


def _seed(data_dir: Path, revision: str) -> None:
    seeder = SEEDERS.get(revision)
    if seeder is None:
        return
    engine = make_engine(db_path(data_dir))
    try:
        with _schema_as_it_stood(engine), make_session_factory(engine).begin() as session:
            seeder(session, data_dir)
    finally:
        engine.dispose()


# --- snapshot and comparison ---------------------------------------------------------------


@dataclass(frozen=True)
class Table:
    columns: tuple[tuple[object, ...], ...]  # PRAGMA table_info minus the cid
    rows: tuple[tuple[object, ...], ...]


@dataclass(frozen=True)
class Snapshot:
    tables: dict[str, Table]
    triggers: dict[str, str]
    indexes: dict[str, str]


def snapshot(data_dir: Path) -> Snapshot:
    conn = sqlite3.connect(db_path(data_dir))
    try:
        names = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                " AND name <> 'alembic_version' ORDER BY name"
            )
        ]
        tables = {}
        for name in names:
            columns = tuple(tuple(r[1:]) for r in conn.execute(f'PRAGMA table_info("{name}")'))
            query = f'SELECT * FROM "{name}" ORDER BY rowid'  # noqa: S608 - names from sqlite_master
            rows = tuple(tuple(r) for r in conn.execute(query))
            tables[name] = Table(columns, rows)
        triggers = dict(conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'trigger'"))
        indexes = dict(
            conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL"
            )
        )
        return Snapshot(tables, triggers, indexes)
    finally:
        conn.close()


#: revision -> (table, column, value): rows the revision converts and retires on purpose. Only
#: these rows may be missing afterwards; every other row of the table must be untouched.
RETIRES: dict[str, tuple[str, str, object]] = {"0019": ("assumption", "key", "annual_spending")}

#: revision -> tables the revision converts into others and drops on purpose (their rows are
#: proved to have arrived by the test of that conversion, not by the additive check).
CONVERTS: dict[str, tuple[str, ...]] = {"0028": ("tax_evidence", "tax_record_evidence")}


def assert_additive(
    before: Snapshot,
    after: Snapshot,
    retires: tuple[str, str, object] | None = None,
    converted: tuple[str, ...] = (),
) -> None:
    """Everything in ``before`` is still there, untouched. New tables, indexes, triggers and
    appended columns are fine; a changed, dropped, retyped or renamed column or any changed row is
    not. Rows are compared exactly, so a migration that inserts into an existing table must say so
    by relaxing this on purpose."""
    for name, old in before.tables.items():
        if name in converted:
            assert name not in after.tables, f"table {name} should have been converted away"
            continue
        assert name in after.tables, f"table {name} was dropped"
        new = after.tables[name]
        width = len(old.columns)
        assert new.columns[:width] == old.columns, f"table {name}: existing columns changed"
        old_rows = old.rows
        if retires is not None and retires[0] == name:
            at = [c[0] for c in old.columns].index(retires[1])
            old_rows = tuple(r for r in old.rows if r[at] != retires[2])
        assert len(new.rows) == len(old_rows), (
            f"table {name}: {len(old_rows)} rows became {len(new.rows)}"
        )
        for i, (was, now) in enumerate(zip(old_rows, new.rows, strict=True)):
            assert now[:width] == was, f"table {name}: row {i} changed: {was!r} -> {now[:width]!r}"
    for name, sql in before.triggers.items():
        assert after.triggers.get(name) == sql, f"trigger {name} was dropped or changed"
    for name, sql in before.indexes.items():
        if any(f" ON {table} " in sql for table in converted):  # went with its table
            continue
        assert after.indexes.get(name) == sql, f"index {name} was dropped or changed"


def assert_rows_kept(after: Table, before: Table, name: str) -> None:
    """A downgrade keeps every row. The columns a downgrade dropped (0023's ``effort_level``) are
    not compared: the rows are compared at the width that is left."""
    width = len(after.columns)
    expected = [r[:width] for r in before.rows]
    if name == "rule_capture":  # 0030's downgrade makes a carried capture typed: tested apart
        at = [c[0] for c in after.columns].index("method")
        expected = [
            r[:at] + ("typed" if r[at] == "carried" else r[at],) + r[at + 1 :] for r in expected
        ]
    assert expected == list(after.rows), name


def head_as_of_0027(data_dir: Path) -> Snapshot:
    """The head snapshot with the Library tables shown as 0027 held them, for the downgrade tests
    that pass back through 0028: ``library_document`` is ``tax_evidence`` (same leading columns)
    and the ``tax_record`` links are ``tax_record_evidence``."""
    head = snapshot(data_dir)
    documents, links = head.tables["library_document"], head.tables["document_link"]
    columns = [c[0] for c in links.columns]
    kind, target, doc, added = (
        columns.index(c) for c in ("target_kind", "target_id", "document_id", "added_at")
    )
    tables = {
        name: table
        for name, table in head.tables.items()
        if name not in ("library_document", "document_link")
    }
    tables["tax_evidence"] = documents
    tables["tax_record_evidence"] = Table(
        links.columns[:3],
        tuple((r[target], r[doc], r[added]) for r in links.rows if r[kind] == "tax_record"),
    )
    return Snapshot(tables, head.triggers, head.indexes)


def _steps(scripts: Path) -> list[str]:
    """Revisions after the baseline, oldest first."""
    script = ScriptDirectory(str(scripts))
    revisions = [r.revision for r in script.walk_revisions()][::-1]
    return revisions[revisions.index(BASELINE) + 1 :]


def upgrade_populated(data_dir: Path, scripts: Path = MIGRATIONS_DIR) -> tuple[Snapshot, Snapshot]:
    """Build a populated baseline database, then upgrade it to head one step at a time, asserting
    each step is additive and seeding what that step created. Returns (baseline, final)."""
    data_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURE, data_dir / "household.json")
    _without_effort_level(data_dir / "household.json")
    config = alembic_config(data_dir, scripts)
    command.upgrade(config, BASELINE)
    _seed(data_dir, BASELINE)
    baseline = current = snapshot(data_dir)
    assert current.tables["valuation"].rows, "the synthetic household should have Valuations"
    for revision in _steps(scripts):
        command.upgrade(config, revision)
        upgraded = snapshot(data_dir)
        assert_additive(current, upgraded, RETIRES.get(revision), CONVERTS.get(revision, ()))
        _seed(data_dir, revision)
        current = snapshot(data_dir)
    return baseline, current


# --- the tests -----------------------------------------------------------------------------


def test_the_baseline_is_the_last_pre_m2_revision() -> None:
    script = ScriptDirectory(str(MIGRATIONS_DIR))
    assert script.get_revision(BASELINE) is not None
    assert BASELINE in [r.revision for r in script.walk_revisions()]


def test_upgrading_a_populated_database_changes_no_existing_row(tmp_path: Path) -> None:
    baseline, final = upgrade_populated(tmp_path)
    # Every step was already asserted additive. End to end: the pre-M2 tables hold exactly the
    # pre-M2 rows. Seeding the M2 tables from the fixture writes audit events (the one existing
    # table that grows), so the audit trail is compared by its pre-existing prefix.
    for name, table in baseline.tables.items():
        if name not in ("audit_event", "meta"):
            width = len(table.columns)  # 0023 appended a column to ``household``
            rows = [r[:width] for r in final.tables[name].rows]
            if name == "valuation":  # 0027's two statements each recorded their 30 June Valuation
                assert rows[: len(table.rows)] == list(table.rows), name
                assert len(rows) == len(table.rows) + 2, name
            else:
                assert rows == list(table.rows), name
    # The seeders add their own version keys to ``meta`` (``assumption_seed_version``); the
    # pre-existing keys keep their values.
    assert set(baseline.tables["meta"].rows) <= set(final.tables["meta"].rows)
    old_audit = baseline.tables["audit_event"].rows
    assert final.tables["audit_event"].rows[: len(old_audit)] == old_audit
    assert len(final.tables["audit_event"].rows) > len(old_audit)  # the fixture Instruments, Prices
    assert {
        "instrument",
        "instrument_identifier",
        "price",
        "import_source",
        "import",
        "staged_row",
        "account_identifier",
        "transaction",
        "fetch_run",
        "scenario",
        "assumption",
        "projection_run",
        "spending_phase",
        "income_stream",
        "income_stream_owner",
        "tax_record",
        "library_document",
        "document_link",
        "due_snooze",
        "rule_capture",
        "super_statement",
        "super_statement_option",
        "document_profile",
        "tax_whatif",
        "tax_estimate",
        "tax_lodged_result",
    } <= final.tables.keys()
    assert len(final.tables["tax_lodged_result"].rows) == 2, "the Lodged result step's seeder ran"
    assert len(final.tables["tax_whatif"].rows) == 2, "the Tax estimate step's seeder ran"
    assert len(final.tables["tax_estimate"].rows) == 1
    assert len(final.tables["document_profile"].rows) == 1, "the profile step's seeder ran"
    assert len(final.tables["super_statement"].rows) == 2, "the Super statement step's seeder ran"
    assert len(final.tables["super_statement_option"].rows) == 3
    valuation_columns = [c[0] for c in final.tables["valuation"].columns]
    statement_columns = [c[0] for c in final.tables["super_statement"].columns]
    valuations_by_id = {r[0]: r for r in final.tables["valuation"].rows}
    for row in final.tables["super_statement"].rows:
        recorded = valuations_by_id[row[statement_columns.index("valuation_id")]]
        assert recorded[valuation_columns.index("source")] == "statement"
        assert (
            recorded[valuation_columns.index("as_at")]
            == (row[statement_columns.index("period_end")])
        )
        assert (
            recorded[valuation_columns.index("amount_minor")]
            == (row[statement_columns.index("closing_minor")])
        )
    assert not {"tax_evidence", "tax_record_evidence"} & final.tables.keys(), "0028 converted them"
    assert len(final.tables["library_document"].rows) == 2, (
        "the Tax evidence step's receipt, converted, and the Library step's note"
    )
    assert len(final.tables["document_link"].rows) == 2
    assert len(final.tables["income_stream"].rows) == 2, "the income stream step's seeder ran"
    assert len(final.tables["tax_record"].rows) == 24, "the Tax record step's seeder ran"
    assert len(final.tables["due_snooze"].rows) == 1, "the What's due step's seeder ran"
    assert len(final.tables["rule_capture"].rows) == 3, (
        "the Rule capture step's seeder ran, and so did the matched and carried method steps'"
    )
    household_row = final.tables["household"].rows[0]
    assert household_row[3] == "steady", "the Effort level was set from the fixture"
    assert baseline.tables["household"].rows[0] == household_row[:3]
    assert household_row[4:6] == (None, None), "couple status and homeowner stay not entered"
    home = [r for r in final.tables["account"].rows if r[1] == "Family Home"]
    assert household_row[6] == home[0][0], "the principal home was set from the fixture"
    assert len(final.tables["income_stream_owner"].rows) == 2
    assert final.tables["instrument"].rows, "the step's seeder ran"
    assert final.tables["price"].rows, "the price step's seeder ran"
    assert final.tables["import"].rows, "the import step's seeder ran"
    assert len(final.tables["staged_row"].rows) == 5 + 14  # the 0012 trades, the 0014 cash file
    assert final.tables["account_identifier"].rows, "the identifier step's seeder ran"
    assert len(final.tables["transaction"].rows) == 14, "the transaction step's seeder ran"
    assert final.tables["fetch_run"].rows, "the fetch run step's seeder ran"
    assert len(final.tables["scenario"].rows) == 6, (
        "the 0016 one, Best, Likely, Worst, and the two the 0018 seeder wrote by hand"
    )
    assert final.tables["assumption"].rows, "the scenario step's seeder ran"
    assert len(final.tables["projection_run"].rows) == 2, "the 0017 run and the 0019 seeder's run"
    scenario_columns = [c[0] for c in final.tables["scenario"].columns]
    assert scenario_columns[-1] == "drawdown_order", "0018 appended the column"
    orders = {r[scenario_columns.index("name")]: r[-1] for r in final.tables["scenario"].rows}
    assert orders["Upgrade check scenario"] is None and orders["Likely"] is None
    assert orders[EARLY] is not None


# --- the annual_spending conversion (ADR-0017 section 6) -----------------------

#: Captured by running the code as it was before spending phases, on this same household: the four
#: Scenarios' rows, funds-last age, "lasts" flag and sustainable income, as one sha256 of their
#: canonical JSON, at as-at 2026-10-25. A wrong conversion (a different anchor or offset, a lost or
#: crossed layer) moves at least one of them.
GOLDEN_BEFORE_PHASES = {
    "Best": "5e58afd6db56d6cfcabf5741e787054f2f66a4a127a961bb414bd1e8b19d3854",
    "Likely": "122e7207a20d2d64e3d6ed28e2783eaeadffe52a6127a7da69bd171a1b65bb3c",
    "Worst": "3b6a6660e02f75f545127227a40f7da8697775c8e9bf37590de17fde4d534dd1",
    EARLY: "0af2297be951f442e9ad4190d3c875783fbfb07db66a9e6c5aab0f4aedaaf905",
}


def _without_streams(data_dir: Path) -> None:
    """The conversion goldens are of the household before salaries: drop the 0020 seeder's two."""
    conn = sqlite3.connect(db_path(data_dir))
    try:
        conn.execute("DELETE FROM income_stream_owner")
        conn.execute("DELETE FROM income_stream")
        conn.commit()
    finally:
        conn.close()


def _projection_digest(session: Session, scenario_id: int) -> str:
    import hashlib
    import json

    from finance360.api.projection import _out
    from finance360.capabilities.projection import service

    run = service.run_scenario(session, scenario_id, date(2026, 10, 25))
    core = _out(run.run, run.spending_layer).model_dump(
        mode="json", include={"rows", "funds_last_to_age", "lasts_beyond_horizon", "sustainable"}
    )
    # Engine 2 added line debits (ADR-0028). The golden stays the one worked before them, so it
    # drops the new fields: with no debits every figure must still be exactly as it was.
    for row in core["rows"]:
        assert row.pop("debits") == [] and row.pop("paid_personally") == "0.00"
        for category in row["categories"]:
            assert category.pop("debited") == "0.00"
    return hashlib.sha256(json.dumps(core, sort_keys=True).encode()).hexdigest()


def test_the_conversion_makes_each_annual_spending_row_a_phase_in_its_own_layer(
    tmp_path: Path,
) -> None:
    upgrade_populated(tmp_path)
    conn = sqlite3.connect(db_path(tmp_path))
    try:
        phases = conn.execute(
            "SELECT s.name, p.anchor, p.offset_years, p.amount_minor, p.amount_ccy "
            "FROM spending_phase p LEFT JOIN scenario s ON s.id = p.scenario_id ORDER BY p.id"
        ).fetchall()
        left = conn.execute("SELECT count(*) FROM assumption WHERE key = 'annual_spending'")
        scenarios_with_rows = {r[0] for r in phases}
        assert left.fetchone() == (0,), "the key is retired"
    finally:
        conn.close()
    # The Household default, the Scenario that overrode it, and the Scenario that spends nothing
    # (kept as a 0 phase, not dropped). Best, Likely and Worst never stated it: no rows, they
    # inherit the default list.
    assert phases == [
        (None, "last_retirement", 0, 7_000_000, "AUD"),
        (EARLY, "last_retirement", 0, 8_500_000, "AUD"),
        (NOTHING, "last_retirement", 0, 0, "AUD"),
    ]
    assert scenarios_with_rows.isdisjoint({"Best", "Likely", "Worst"})


def test_the_conversion_changes_no_projection(tmp_path: Path) -> None:
    """Golden: every Scenario projects exactly as it did before spending phases existed."""
    from sqlalchemy import select

    from finance360.core.models.scenario import Scenario

    upgrade_populated(tmp_path)
    _without_streams(tmp_path)
    engine = make_engine(db_path(tmp_path))
    try:
        with make_session_factory(engine).begin() as session:
            ids = {sc.name: sc.id for sc in session.scalars(select(Scenario))}
            got = {name: _projection_digest(session, ids[name]) for name in GOLDEN_BEFORE_PHASES}
    finally:
        engine.dispose()
    assert got == GOLDEN_BEFORE_PHASES


def test_downgrading_0019_restores_each_annual_spending_row(tmp_path: Path) -> None:
    """The way back: every ``(last_retirement, 0)`` phase becomes the ``annual_spending`` row it
    came from, in its own layer, and ``spending_phase`` is gone. Upgrading again is clean."""
    upgrade_populated(tmp_path)
    config = alembic_config(tmp_path, MIGRATIONS_DIR)
    command.downgrade(config, "0018")
    conn = sqlite3.connect(db_path(tmp_path))
    try:
        restored = conn.execute(
            "SELECT s.name, a.scope_type, a.scope_ref, a.value_minor, a.value_ccy "
            "FROM assumption a LEFT JOIN scenario s ON s.id = a.scenario_id "
            "WHERE a.key = 'annual_spending' ORDER BY a.id"
        ).fetchall()
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert restored == [
        (None, "household", "", 7_000_000, "AUD"),
        (EARLY, "household", "", 8_500_000, "AUD"),
        (NOTHING, "household", "", 0, "AUD"),
    ]
    assert "spending_phase" not in tables
    command.upgrade(config, "head")
    command.check(config)


def test_downgrading_0020_drops_only_the_income_stream_tables(tmp_path: Path) -> None:
    """The way back is clean: the two new tables go, nothing else moves, upgrading again works."""
    upgrade_populated(tmp_path)
    before = head_as_of_0027(tmp_path)
    config = alembic_config(tmp_path, MIGRATIONS_DIR)
    command.downgrade(config, "0019")
    after = snapshot(tmp_path)
    assert "income_stream" not in after.tables and "income_stream_owner" not in after.tables
    for name, table in after.tables.items():
        if name != "audit_event":  # the fixture streams' audit rows stay: the trail is append-only
            assert_rows_kept(table, before.tables[name], name)
    command.upgrade(config, "head")
    command.check(config)


def test_downgrading_0021_drops_only_the_tax_record_table(tmp_path: Path) -> None:
    upgrade_populated(tmp_path)
    before = head_as_of_0027(tmp_path)
    config = alembic_config(tmp_path, MIGRATIONS_DIR)
    command.downgrade(config, "0020")
    after = snapshot(tmp_path)
    assert "tax_record" not in after.tables
    assert not {n for n in after.indexes if "tax_record" in n}
    for name, table in after.tables.items():
        if name != "audit_event":  # the fixture records' audit rows stay: the trail is append-only
            assert_rows_kept(table, before.tables[name], name)
    command.upgrade(config, "head")
    command.check(config)


def test_downgrading_0022_drops_only_the_tax_evidence_tables(tmp_path: Path) -> None:
    upgrade_populated(tmp_path)
    before = head_as_of_0027(tmp_path)
    config = alembic_config(tmp_path, MIGRATIONS_DIR)
    command.downgrade(config, "0021")
    after = snapshot(tmp_path)
    assert not {"tax_evidence", "tax_record_evidence"} & after.tables.keys()
    assert not {n for n in after.indexes if "tax_record_evidence" in n or "tax_evidence" in n}
    for name, table in after.tables.items():
        if name != "audit_event":  # the attach's audit row stays: the trail is append-only
            assert_rows_kept(table, before.tables[name], name)
    command.upgrade(config, "head")
    command.check(config)


def test_downgrading_0023_drops_the_snooze_table_and_the_effort_level_only(tmp_path: Path) -> None:
    upgrade_populated(tmp_path)
    before = head_as_of_0027(tmp_path)
    config = alembic_config(tmp_path, MIGRATIONS_DIR)
    command.downgrade(config, "0022")
    after = snapshot(tmp_path)
    assert "due_snooze" not in after.tables
    assert [c[0] for c in after.tables["household"].columns] == ["id", "name", "time_zone"]
    for name, table in after.tables.items():
        if name != "audit_event":  # the trail is append-only
            assert_rows_kept(table, before.tables[name], name)
    command.upgrade(config, "head")
    command.check(config)


def test_downgrading_0024_drops_only_the_rule_capture_table(tmp_path: Path) -> None:
    upgrade_populated(tmp_path)
    before = head_as_of_0027(tmp_path)
    config = alembic_config(tmp_path, MIGRATIONS_DIR)
    command.downgrade(config, "0023")
    after = snapshot(tmp_path)
    assert "rule_capture" not in after.tables
    assert not {n for n in after.indexes if "rule_capture" in n}
    assert not {n for n in after.triggers if "rule_capture" in n}
    for name, table in after.tables.items():
        if name != "audit_event":  # the capture's audit row stays: the trail is append-only
            assert_rows_kept(table, before.tables[name], name)
    command.upgrade(config, "head")
    command.check(config)


def test_downgrading_0025_drops_only_the_ai_log_table(tmp_path: Path) -> None:
    upgrade_populated(tmp_path)
    before = head_as_of_0027(tmp_path)
    assert len(before.tables["ai_log"].rows) == 1
    config = alembic_config(tmp_path, MIGRATIONS_DIR)
    command.downgrade(config, "0024")
    after = snapshot(tmp_path)
    assert "ai_log" not in after.tables
    assert not {n for n in after.indexes if "ai_log" in n}
    for name, table in after.tables.items():
        if name == "rule_capture":  # 0026's downgrade makes a matched capture typed: tested apart
            continue
        if name != "audit_event":
            assert_rows_kept(table, before.tables[name], name)
    command.upgrade(config, "head")
    command.check(config)


def test_the_rebuilt_rule_capture_table_keeps_its_rows_its_triggers_and_its_index(
    tmp_path: Path,
) -> None:
    """0026 rebuilds ``rule_capture`` to widen a CHECK. Nothing else about it may move: the typed
    row from 0024 is identical (``assert_additive`` proved that at the step), the append-only
    triggers and the one-active-row index are the same SQL, the new CHECK admits ``matched`` and
    still refuses a method nobody defined."""
    upgrade_populated(tmp_path)
    after = snapshot(tmp_path)
    assert {"rule_capture_no_delete", "rule_capture_no_update"} <= after.triggers.keys()
    assert "uq_rule_capture_active" in after.indexes
    columns = [c[0] for c in after.tables["rule_capture"].columns]
    methods = [r[columns.index("method")] for r in after.tables["rule_capture"].rows]
    assert methods == ["typed", "matched", "carried"]
    matched = after.tables["rule_capture"].rows[1]
    assert matched[columns.index("ai_log_id")] is None
    assert matched[columns.index("ai_value_json")] is not None
    carried = after.tables["rule_capture"].rows[2]  # 0030's: provisional, no quote, no AI
    assert carried[columns.index("provisional_note")] is not None
    assert carried[columns.index("quote")] is None and carried[columns.index("ai_log_id")] is None
    conn = sqlite3.connect(db_path(tmp_path))
    try:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute("DELETE FROM rule_capture")
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute("UPDATE rule_capture SET quote = 'tampered'")
        with pytest.raises(sqlite3.IntegrityError, match="method_known"):
            conn.execute(
                "INSERT INTO rule_capture (jurisdiction, key, fy, value_json, retrieved_on, "
                "method, captured_at, captured_kind, captured_by) VALUES ('au', 'k', 2027, '1', "
                "'2026-11-07', 'guessed', '2026-11-07 00:00:00', 'owner', 'login:1')"
            )
    finally:
        conn.close()


def test_the_upgraded_database_keeps_a_saved_estimate_and_still_refuses_to_change_it(
    tmp_path: Path,
) -> None:
    """0031 adds ``tax_estimate``. After the whole chain, its seeded row is readable and unchanged
    by later steps and the append-only triggers are there."""
    upgrade_populated(tmp_path)
    after = snapshot(tmp_path)
    assert {"tax_estimate_no_update", "tax_estimate_no_delete"} <= after.triggers.keys()
    conn = sqlite3.connect(db_path(tmp_path))
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        count = conn.execute("SELECT count(*) FROM tax_estimate").fetchone()[0]
        assert count == 1
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute("UPDATE tax_estimate SET label = 'tampered'")
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute("DELETE FROM tax_estimate")
        assert conn.execute("SELECT count(*) FROM tax_estimate").fetchone()[0] == 1
    finally:
        conn.close()


def test_downgrading_0026_makes_a_matched_capture_typed_and_keeps_everything_else(
    tmp_path: Path,
) -> None:
    upgrade_populated(tmp_path)
    config = alembic_config(tmp_path, MIGRATIONS_DIR)
    command.downgrade(config, "0026")  # 0027's own downgrade is tested apart
    before = snapshot(tmp_path)
    command.downgrade(config, "0025")
    after = snapshot(tmp_path)
    columns = [c[0] for c in before.tables["rule_capture"].columns]
    at = columns.index("method")
    assert [r[at] for r in after.tables["rule_capture"].rows] == ["typed", "typed", "typed"]
    for name, table in after.tables.items():
        if name == "rule_capture":  # only the method column differs, and only on the matched row
            old = [r[:at] + r[at + 1 :] for r in before.tables[name].rows]
            assert [r[:at] + r[at + 1 :] for r in table.rows] == old
        elif name != "audit_event":
            assert_rows_kept(table, before.tables[name], name)
    assert after.triggers == before.triggers and after.indexes == before.indexes
    command.upgrade(config, "head")
    command.check(config)


def test_the_upgraded_database_keeps_both_lodged_results_and_still_refuses_to_change_them(
    tmp_path: Path,
) -> None:
    """0032 adds ``tax_lodged_result``. After the whole chain, the seeded notice and its amendment
    are readable (the earlier one superseded, not lost) and the append-only triggers are there."""
    upgrade_populated(tmp_path)
    after = snapshot(tmp_path)
    assert {"tax_lodged_result_no_update", "tax_lodged_result_no_delete"} <= after.triggers.keys()
    conn = sqlite3.connect(db_path(tmp_path))
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        rows = conn.execute(
            "SELECT id, superseded_at IS NULL, superseded_by_id, items_json "
            "FROM tax_lodged_result ORDER BY id"
        ).fetchall()
        assert [(r[0], r[1], r[2]) for r in rows] == [(1, 0, 2), (2, 1, None)]
        assert '"taxable_income":{"ccy":"AUD","minor":8560600}' in rows[0][3]
        assert '"taxable_income":{"ccy":"AUD","minor":8510600}' in rows[1][3]
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute("UPDATE tax_lodged_result SET items_json = '{}'")
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute("DELETE FROM tax_lodged_result")
        assert conn.execute("SELECT count(*) FROM tax_lodged_result").fetchone()[0] == 2
    finally:
        conn.close()


def test_downgrading_0033_drops_only_the_household_fact_columns(tmp_path: Path) -> None:
    upgrade_populated(tmp_path)
    before = snapshot(tmp_path)
    household_row = before.tables["household"].rows[0]
    assert household_row[4:6] == (None, None) and household_row[6] is not None
    config = alembic_config(tmp_path, MIGRATIONS_DIR)
    command.downgrade(config, "0032")
    after = snapshot(tmp_path)
    names = [c[0] for c in after.tables["household"].columns]
    assert names == ["id", "name", "time_zone", "effort_level"]
    assert after.tables["household"].rows == (household_row[:4],)
    for name, table in after.tables.items():
        if name != "household":  # compared above
            assert table.rows == before.tables[name].rows, name
    command.upgrade(config, "head")
    command.check(config)


def test_downgrading_0032_drops_only_the_lodged_result_table(tmp_path: Path) -> None:
    upgrade_populated(tmp_path)
    before = snapshot(tmp_path)
    assert len(before.tables["tax_lodged_result"].rows) == 2
    config = alembic_config(tmp_path, MIGRATIONS_DIR)
    command.downgrade(config, "0031")
    after = snapshot(tmp_path)
    assert "tax_lodged_result" not in after.tables
    assert not {n for n in after.triggers if n.startswith("tax_lodged_result")}
    assert after.indexes == {
        k: v for k, v in before.indexes.items() if "tax_lodged_result" not in k
    }
    for name, table in after.tables.items():
        if name != "audit_event":  # the notices' audit rows stay: the trail is append-only
            width = len(table.columns)  # household lost 0033's columns
            assert table.rows == tuple(r[:width] for r in before.tables[name].rows), name
    command.upgrade(config, "head")
    command.check(config)


def test_downgrading_0031_drops_only_the_tax_estimate_tables(tmp_path: Path) -> None:
    upgrade_populated(tmp_path)
    before = snapshot(tmp_path)
    assert len(before.tables["tax_estimate"].rows) == 1
    config = alembic_config(tmp_path, MIGRATIONS_DIR)
    command.downgrade(config, "0030")
    after = snapshot(tmp_path)
    assert not {"tax_whatif", "tax_estimate", "tax_lodged_result"} & after.tables.keys()
    assert not {n for n in after.triggers if n.startswith("tax_estimate")}
    # 0032's index went too, passing back through its downgrade
    assert after.indexes == {
        k: v for k, v in before.indexes.items() if "tax_lodged_result" not in k
    }
    for name, table in after.tables.items():
        if name != "audit_event":  # the estimate's audit rows stay: the trail is append-only
            width = len(table.columns)  # household lost 0033's columns
            assert table.rows == tuple(r[:width] for r in before.tables[name].rows), name
    command.upgrade(config, "head")
    command.check(config)


def test_downgrading_0030_makes_a_carried_capture_typed_and_keeps_everything_else(
    tmp_path: Path,
) -> None:
    # Live-data rule: 0030 rebuilds the table that holds every captured figure. Going back must
    # keep every row, its value and its provisional note, and the triggers and index.
    upgrade_populated(tmp_path)
    before = snapshot(tmp_path)
    config = alembic_config(tmp_path, MIGRATIONS_DIR)
    command.downgrade(config, "0029")
    after = snapshot(tmp_path)
    columns = [c[0] for c in before.tables["rule_capture"].columns]
    at = columns.index("method")
    assert [r[at] for r in before.tables["rule_capture"].rows][-1] == "carried"
    assert [r[at] for r in after.tables["rule_capture"].rows] == ["typed", "matched", "typed"]
    old = [r[:at] + r[at + 1 :] for r in before.tables["rule_capture"].rows]
    assert [r[:at] + r[at + 1 :] for r in after.tables["rule_capture"].rows] == old
    # 0031's two triggers went with its tables, passing back through its downgrade
    assert after.triggers == {
        k: v
        for k, v in before.triggers.items()
        if not k.startswith(("tax_estimate_", "tax_lodged_result_"))
    }
    assert after.indexes == {
        k: v for k, v in before.indexes.items() if "tax_lodged_result" not in k
    }
    for name, table in after.tables.items():
        if name not in ("rule_capture", "audit_event"):
            assert_rows_kept(table, before.tables[name], name)
    conn = sqlite3.connect(db_path(tmp_path))
    try:  # the narrower CHECK is back: a carried capture is refused until 0030 is applied again
        with pytest.raises(sqlite3.IntegrityError, match="method_known"):
            conn.execute(
                "INSERT INTO rule_capture (jurisdiction, key, fy, value_json, retrieved_on, "
                "method, captured_at, captured_kind, captured_by) VALUES ('au', 'k', 2027, '1', "
                "'2026-11-14', 'carried', '2026-11-14 00:00:00', 'owner', 'login:1')"
            )
    finally:
        conn.close()
    command.upgrade(config, "head")
    command.check(config)
    assert [r[at] for r in snapshot(tmp_path).tables["rule_capture"].rows][:2] == [
        "typed",
        "matched",
    ]


def test_downgrading_0027_drops_only_the_super_statement_tables(tmp_path: Path) -> None:
    upgrade_populated(tmp_path)
    before = head_as_of_0027(tmp_path)
    assert len(before.tables["super_statement"].rows) == 2
    config = alembic_config(tmp_path, MIGRATIONS_DIR)
    command.downgrade(config, "0026")
    after = snapshot(tmp_path)
    assert not {n for n in after.tables if n.startswith("super_statement")}
    assert not {n for n in after.indexes if "super_statement" in n}
    assert not {n for n in after.triggers if "super_statement" in n}
    for name, table in after.tables.items():
        if name != "audit_event":  # the statements' audit rows stay: the trail is append-only
            assert_rows_kept(table, before.tables[name], name)  # Valuations included
    command.upgrade(config, "head")
    command.check(config)


def test_the_upgrade_to_the_library_keeps_the_evidence_row_its_link_and_its_file(
    tmp_path: Path,
) -> None:
    """Live data: the fixture's receipt was attached by the previous release (a row, a link and a
    file under ``documents/tax/``). After the upgrade it is a Library document under the same id,
    linked to the same Tax record, and start-up adoption moves the file with its bytes intact."""
    import hashlib

    from sqlalchemy import select

    from finance360.capabilities.library import files
    from finance360.capabilities.library import service as library
    from finance360.core.models.library import DocumentLink, LibraryDocument

    upgrade_populated(tmp_path)
    receipt = (FIXTURE.parent / "evidence" / "sample_donation_receipt.txt").read_bytes()
    digest = hashlib.sha256(receipt).hexdigest()
    old = tmp_path / "documents" / "tax" / digest[:2] / digest
    assert old.read_bytes() == receipt, "the previous release's file was there before the upgrade"

    engine = make_engine(db_path(tmp_path))
    try:
        with make_session_factory(engine).begin() as session:
            docs = list(session.scalars(select(LibraryDocument).order_by(LibraryDocument.id)))
            converted = docs[0]
            assert (converted.id, converted.sha256, converted.file_name) == (
                1,
                digest,
                "sample_donation_receipt.txt",
            )
            assert (converted.media_type, converted.size_bytes) == ("text/plain", len(receipt))
            assert (converted.text, converted.text_status) == (None, "none")
            [link] = session.scalars(
                select(DocumentLink).where(DocumentLink.document_id == converted.id)
            )
            assert link.target_kind == "tax_record"
            # not yet adopted: it is read from the old path, and the check passes
            assert library.document_file(session, tmp_path, converted.id)[1] == receipt
            assert library.check(session, tmp_path).problems == ()

            report = library.adopt_files(session, tmp_path)
            assert (report.moved, report.problems) == (1, ())  # the 0028 note was stored in place
            assert library.adopt_files(session, tmp_path).moved == 0  # a second run: no change
            assert library.document_file(session, tmp_path, converted.id)[1] == receipt
    finally:
        engine.dispose()
    assert files.path_for(tmp_path, digest).read_bytes() == receipt and not old.exists()


def test_downgrading_0028_rebuilds_the_tax_evidence_tables_from_the_library(
    tmp_path: Path,
) -> None:
    upgrade_populated(tmp_path)
    before = head_as_of_0027(tmp_path)
    assert len(before.tables["tax_evidence"].rows) == 2  # the receipt and the Library step's note
    assert len(before.tables["tax_record_evidence"].rows) == 2
    config = alembic_config(tmp_path, MIGRATIONS_DIR)
    command.downgrade(config, "0027")
    after = snapshot(tmp_path)
    assert not {"library_document", "document_link"} & after.tables.keys()
    assert not {n for n in after.indexes if "document_link" in n or "library_document" in n}
    for name, table in after.tables.items():
        if name != "audit_event":  # the add and link audit rows stay: the trail is append-only
            assert_rows_kept(table, before.tables[name], name)
    command.upgrade(config, "head")
    command.check(config)


def test_a_scenario_that_spent_nothing_still_spends_nothing(tmp_path: Path) -> None:
    from sqlalchemy import select

    from finance360.capabilities.projection import scenarios, service
    from finance360.core.models.scenario import Scenario

    upgrade_populated(tmp_path)
    _without_streams(tmp_path)
    engine = make_engine(db_path(tmp_path))
    try:
        with make_session_factory(engine).begin() as session:
            nothing = session.scalars(select(Scenario).where(Scenario.name == NOTHING)).one()
            _, resolved = scenarios.resolve_for(session, nothing.id)
            request = service.ProjectionRequest(
                inflation=resolved.inflation,
                returns=resolved.returns,
                retirement_ages=resolved.retirement_ages,
                spending_phases=(),  # what a stored 0 meant: no outflow at all
                annual_saving=resolved.annual_saving,
                horizon_age=resolved.horizon_age,
                as_at=date(2026, 10, 25),
                rule_overrides=resolved.rule_overrides,
            )
            expected = service.run(session, request)
            actual = service.run_scenario(session, nothing.id, date(2026, 10, 25)).run
    finally:
        engine.dispose()
    assert actual.prepared.input.outflows == () == expected.prepared.input.outflows
    assert actual.result == expected.result


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_event SET summary = 'tampered'",
        "DELETE FROM audit_event",
        "UPDATE valuation SET provenance_note = 'tampered'",
        "DELETE FROM valuation",
        "UPDATE fx_rate SET rate = 1",
        "DELETE FROM fx_rate",
        "UPDATE price SET provenance_note = 'tampered'",
        "DELETE FROM price",
        "UPDATE account_identifier SET value = 'tampered'",
        "DELETE FROM account_identifier",
        'UPDATE "transaction" SET amount_minor = 1',
        'UPDATE "transaction" SET import_id = NULL',
        'DELETE FROM "transaction"',
        "UPDATE projection_run SET label = 'tampered'",
        "DELETE FROM projection_run",
    ],
)
def test_the_append_only_triggers_still_hold_after_the_upgrade(
    tmp_path: Path, statement: str
) -> None:
    upgrade_populated(tmp_path)
    conn = sqlite3.connect(db_path(tmp_path))
    try:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute(statement)
    finally:
        conn.close()


#: what the migration does -> (SQL, the part of the failure message that names the right cause)
BAD_STATEMENTS = {
    "alters a row": ("UPDATE member SET name = name || ' (edited)'", "table member: row 0 changed"),
    "deletes rows": ("DELETE FROM account_tag", "table account_tag: .* rows became 0"),
    "renames a column": (
        "ALTER TABLE member RENAME COLUMN name TO full_name",
        "table member: existing columns changed",
    ),
    "drops a column": (
        "ALTER TABLE member DROP COLUMN date_of_birth",
        "table member: existing columns changed",
    ),
    "drops a table": ("DROP TABLE backup_run", "table backup_run was dropped"),
    "drops a trigger": (
        "DROP TRIGGER audit_event_no_update",
        "trigger audit_event_no_update was dropped",
    ),
}

BAD_MIGRATION = '''"""deliberately destructive: proves the upgrade check bites"""

from alembic import op

revision = "9001"
down_revision = "__HEAD__"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("__SQL__")


def downgrade() -> None:
    pass
'''


@pytest.mark.parametrize(
    ("statement", "reason"), list(BAD_STATEMENTS.values()), ids=list(BAD_STATEMENTS)
)
def test_the_check_fails_when_a_migration_alters_existing_data(
    tmp_path: Path, statement: str, reason: str
) -> None:
    scripts = tmp_path / "scripts"
    shutil.copytree(MIGRATIONS_DIR, scripts, ignore=shutil.ignore_patterns("__pycache__"))
    head = ScriptDirectory(str(MIGRATIONS_DIR)).get_current_head()
    assert head is not None
    (scripts / "versions" / "9001_bad.py").write_text(
        BAD_MIGRATION.replace("__HEAD__", head).replace("__SQL__", statement)
    )
    with pytest.raises(AssertionError, match=reason):
        upgrade_populated(tmp_path / "data", scripts)


def test_a_new_table_and_an_added_nullable_column_are_allowed(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    shutil.copytree(MIGRATIONS_DIR, scripts, ignore=shutil.ignore_patterns("__pycache__"))
    head = ScriptDirectory(str(MIGRATIONS_DIR)).get_current_head()
    assert head is not None
    good = BAD_MIGRATION.replace("__HEAD__", head).replace(
        'op.execute("__SQL__")',
        'op.execute("CREATE TABLE scratch (id INTEGER PRIMARY KEY)")\n'
        '    op.execute("ALTER TABLE member ADD COLUMN nickname TEXT")',
    )
    (scripts / "versions" / "9001_good.py").write_text(good)
    upgrade_populated(tmp_path / "data", scripts)


# --- the catalogue version bump (ADR-0013 section 4) ---------------------------


def test_a_catalogue_version_bump_adds_only_its_new_account_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The live-data guard for the seed-row change. A populated database at head (Accounts,
    Valuations, FX rates, Instruments, Prices, staged and committed sample imports, audit rows),
    seeded with the previous catalogue, is seeded again with the new version. Exactly one
    ``account_type`` row and the ``catalogue_version`` value may differ; every other row of every
    table is byte-identical, and no trigger or index moves."""
    from finance360.core import catalogue

    monkeypatch.setattr(
        catalogue,
        "ACCOUNT_TYPES",
        tuple(t for t in catalogue.ACCOUNT_TYPES if t.key != "investment_property"),
    )
    monkeypatch.setattr(catalogue, "CATALOGUE_VERSION", 2)
    upgrade_populated(tmp_path)  # seeds the baseline with the version 2 catalogue
    before = snapshot(tmp_path)
    monkeypatch.undo()
    assert catalogue.CATALOGUE_VERSION == 3

    engine = make_engine(db_path(tmp_path))
    try:
        with make_session_factory(engine).begin() as session:
            seed_catalogue(session)
    finally:
        engine.dispose()
    after = snapshot(tmp_path)

    for name, table in before.tables.items():
        assert after.tables[name].columns == table.columns, name
        if name not in ("account_type", "meta"):
            assert after.tables[name].rows == table.rows, f"{name} changed"
    # the audit trail in particular: seeding is not audited and rewrites nothing
    assert after.tables["audit_event"].rows == before.tables["audit_event"].rows
    assert before.tables["audit_event"].rows, "the populated database has audit rows"
    # exactly one new account_type row, appended after every existing one, unchanged
    old_types, new_types = before.tables["account_type"].rows, after.tables["account_type"].rows
    assert len(old_types) == 11 and new_types[: len(old_types)] == old_types
    [added] = new_types[len(old_types) :]
    columns = [c[0] for c in after.tables["account_type"].columns]
    row = dict(zip(columns, added, strict=True))
    assert (row["key"], row["name"], row["behaviour_key"], row["seeded"]) == (
        "investment_property",
        "Investment property",
        "simple_balance",
        1,
    )
    # the catalogue version is the only meta value that moved
    meta_columns = [c[0] for c in after.tables["meta"].columns]
    old_meta = {r[meta_columns.index("key")]: r for r in before.tables["meta"].rows}
    new_meta = {r[meta_columns.index("key")]: r for r in after.tables["meta"].rows}
    assert old_meta.keys() == new_meta.keys()
    assert {k for k in old_meta if old_meta[k] != new_meta[k]} == {"catalogue_version"}
    assert new_meta["catalogue_version"][meta_columns.index("value")] == "3"
    assert after.triggers == before.triggers and after.indexes == before.indexes


def test_downgrading_0029_drops_only_the_document_profile_table(tmp_path: Path) -> None:
    """Rows of a saved profile are lost on the way back (the table is new in 0029, and the
    profiles are re-pasteable); nothing else moves and upgrading again works."""
    upgrade_populated(tmp_path)
    before = snapshot(tmp_path)
    assert before.tables["document_profile"].rows
    config = alembic_config(tmp_path, MIGRATIONS_DIR)
    command.downgrade(config, "0028")
    after = snapshot(tmp_path)
    assert "document_profile" not in after.tables
    assert not {n for n in after.indexes | after.triggers if "document_profile" in n}
    for name, table in after.tables.items():
        if name != "audit_event":  # the proposal's audit row stays: the trail is append-only
            assert_rows_kept(table, before.tables[name], name)
    command.upgrade(config, "head")
    command.check(config)
