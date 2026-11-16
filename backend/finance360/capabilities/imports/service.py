"""Import staging service (ADR-0013 section 5).

``detect`` is read-only: it asks the registered Importers to claim a file, parses it, and says which
Account the file's reference resolves to. ``stage`` stores the Import (``staged``) and its rows,
resolving each row's Instrument through the crosswalk; staging the same file again returns the
existing Import and changes nothing. Unresolved references block commit (``check_committable``)
until the owner maps them to an Instrument or creates one. Nothing here writes a Transaction.

The caller owns the transaction. Every mutation writes its audit record in the caller's session.
No message or audit summary contains an account reference.
"""

from __future__ import annotations

import copy
import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from jsonschema import Draft202012Validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from finance360.capabilities.imports import account_identifiers, registry
from finance360.capabilities.imports.importer import (
    UNRECOGNISED,
    AccountReference,
    Importer,
    ImporterError,
    ParsedFile,
)
from finance360.capabilities.reference import instruments as reference_instruments
from finance360.core import audit
from finance360.core.account_identifier import mask_value
from finance360.core.audit import AuditContext
from finance360.core.errors import FieldError, NotFoundError, ValidationError
from finance360.core.instrument import normalise_identifier
from finance360.core.models.account import Account
from finance360.core.models.imports import Import, ImportSource, StagedRow
from finance360.core.models.instrument import Instrument

MAX_FILE_BYTES = 5_000_000
FILE_NAME_MAX = 255


@dataclass(frozen=True)
class Detection:
    importer: Importer
    parsed: ParsedFile
    sha256: str
    #: The Accounts the file's reference resolves to: none, one, or several.
    accounts: list[Account]


@dataclass(frozen=True)
class StageResult:
    import_: Import
    #: False when the same file was already staged or committed for this Import source.
    created: bool


@dataclass(frozen=True)
class UnresolvedReference:
    scheme: str
    ref: str
    name: str | None
    kind_hint: str | None
    row_count: int


@dataclass(frozen=True)
class UnmappedType:
    """A raw Type the Importer does not know, with the staged rows carrying it."""

    raw_type: str
    stream: str
    row_count: int
    #: What the owner may map it to (the Importer's config schema).
    choices: tuple[str, ...]


@dataclass(frozen=True)
class ImportSummary:
    import_: Import
    account: Account
    importer_name: str
    row_count: int
    unresolved_count: int
    unmapped_count: int


@dataclass
class _Ref:
    scheme: str
    ref: str
    name: str | None = None
    kind_hint: str | None = None
    count: int = 0


def _display_name(file_name: str, reference: AccountReference | None) -> str:
    """The name stored, shown and audited. Real export names carry the account number, so it is
    masked like everywhere else an account reference appears."""
    name = " ".join(file_name.split())
    if reference is not None and reference.value:
        name = re.sub(re.escape(reference.value), mask_value(reference.value), name, flags=re.I)
    return name[:FILE_NAME_MAX] or "import.csv"


# --- detection -----------------------------------------------------------------------------


def _rejected(message: str) -> ValidationError:
    return ValidationError([FieldError("file", message)])


def detect(session: Session, content: bytes, file_name: str = "") -> Detection:
    """Which Importer claims the file, what it holds, and which Account its reference names.

    The file name only supplies what the content lacks (the account reference, the stated period);
    recognition never needs it."""
    if not content:
        raise _rejected("The file is empty.")
    if len(content) > MAX_FILE_BYTES:
        raise _rejected(f"The file is larger than {MAX_FILE_BYTES // 1_000_000} MB.")
    claimants = registry.claimants(content, file_name)
    if not claimants:
        raise _rejected(
            "Finance360 does not recognise this file. It can import Vanguard Personal Investor "
            "trade and cash exports."
        )
    if len(claimants) > 1:
        raise _rejected("More than one Importer claims this file, so it cannot be read safely.")
    importer = claimants[0]
    try:
        parsed = importer.parse(content, {}, file_name)
    except ImporterError as problem:
        raise _rejected(str(problem)) from None
    accounts = (
        account_identifiers.find_accounts(session, parsed.account_reference)
        if parsed.account_reference
        else []
    )
    return Detection(importer, parsed, hashlib.sha256(content).hexdigest(), accounts)


# --- Import source -------------------------------------------------------------------------


def _validated_config(importer: Importer, config: dict[str, Any]) -> dict[str, Any]:
    validator: Any = Draft202012Validator(dict(importer.config_schema))
    errors: list[Any] = sorted(validator.iter_errors(config), key=lambda e: list(e.path))
    if errors:
        raise ValidationError([FieldError("config", e.message) for e in errors])
    return config


def _source_for(
    session: Session, context: AuditContext, account: Account, importer: Importer
) -> ImportSource:
    """The Import source for this Account and Importer, created on first use."""

    def existing() -> ImportSource | None:
        return session.scalars(
            select(ImportSource).where(
                ImportSource.account_id == account.id, ImportSource.importer_key == importer.key
            )
        ).first()

    found = existing()
    if found is not None:
        return found
    source = ImportSource(
        account_id=account.id,
        importer_key=importer.key,
        config=_validated_config(importer, {}),
        created_at=_now(),
    )
    try:
        with session.begin_nested():
            session.add(source)
    except IntegrityError:  # another request created it first
        found = existing()
        if found is None:
            raise
        return found
    audit.record(
        session,
        context,
        entity_type="import_source",
        entity_id=source.id,
        action="create",
        after={"id": source.id, "account_id": account.id, "importer": importer.key},
        summary=f"Linked {importer.name} to {account.name}",
    )
    return source


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# --- resolution ----------------------------------------------------------------


def _resolve_instrument(session: Session, scheme: str | None, ref: str | None) -> int | None:
    if not ref or not scheme:
        return None
    found = reference_instruments.resolve(session, ref, scheme)
    return found.id if isinstance(found, Instrument) else None


def _scheme_of(row: StagedRow) -> str | None:
    scheme = row.record.get("instrument_ref_scheme")
    return scheme if isinstance(scheme, str) else None


def unresolved_references(session: Session, import_id: int) -> list[UnresolvedReference]:
    """Distinct references on rows with no Instrument yet, in first-seen order."""
    rows = session.scalars(
        select(StagedRow)
        .where(
            StagedRow.import_id == import_id,
            StagedRow.instrument_ref.is_not(None),
            StagedRow.instrument_id.is_(None),
        )
        .order_by(StagedRow.row_no)
    )
    grouped: dict[tuple[str, str], _Ref] = {}
    for row in rows:
        scheme, ref = _scheme_of(row) or "name", row.instrument_ref or ""
        entry = grouped.setdefault((scheme, normalise_identifier(ref)), _Ref(scheme, ref))
        entry.count += 1
        name = row.record.get("instrument_name")
        hint = row.record.get("instrument_kind_hint")
        entry.name = entry.name or (name if isinstance(name, str) else None)
        entry.kind_hint = entry.kind_hint or (hint if isinstance(hint, str) else None)
    return [
        UnresolvedReference(e.scheme, e.ref, e.name, e.kind_hint, e.count) for e in grouped.values()
    ]


def check_committable(session: Session, import_id: int) -> None:
    """Raise unless the Import is staged with every reference resolved. The commit slice calls
    this first: unresolved references block commit."""
    imp = get_import(session, import_id)
    if imp.status != "staged":
        raise ValidationError([FieldError("status", f"This Import is {imp.status}.")])
    errors: list[FieldError] = []
    unmapped = unmapped_types(session, import_id)
    if unmapped:
        errors.append(
            FieldError(
                "types",
                f"{len(unmapped)} transaction type(s) are not mapped. Map each before committing.",
            )
        )
    pending = unresolved_references(session, import_id)
    if pending:
        errors.append(
            FieldError(
                "references",
                f"{len(pending)} reference(s) are not matched to an Instrument. "
                "Map or create each before committing.",
            )
        )
    if errors:
        raise ValidationError(errors)


# --- unrecognised transaction types ---------------------------------------------------------


def _choices(importer: Importer | None, stream: str) -> tuple[str, ...]:
    """The meanings the Importer's config schema offers for an unknown Type on this Stream."""
    try:
        schema: Any = importer.config_schema if importer else {}
        enum = schema["properties"]["type_map"]["properties"][stream]["additionalProperties"][
            "enum"
        ]
    except (KeyError, TypeError):
        return ()
    return tuple(str(c) for c in enum)


def unmapped_types(session: Session, import_id: int) -> list[UnmappedType]:
    """Distinct raw Types on rows the Importer did not recognise, in first-seen order."""
    imp = get_import(session, import_id)
    source = session.get(ImportSource, imp.import_source_id)
    importer = registry.get(source.importer_key) if source else None
    counts: dict[str, list[Any]] = {}
    rows = session.scalars(
        select(StagedRow).where(StagedRow.import_id == import_id).order_by(StagedRow.row_no)
    )
    for row in rows:
        if row.record.get("kind") != UNRECOGNISED:
            continue
        raw = str(row.record.get("raw_type") or "")
        counts.setdefault(raw.casefold(), [raw, 0])[1] += 1
    choices = _choices(importer, imp.stream)
    return [UnmappedType(raw, imp.stream, n, choices) for raw, n in counts.values()]


def _reapply(session: Session, source: ImportSource, importer: Importer) -> None:
    """Re-parse every staged Import of the source with its current config and refresh the rows.
    Instruments already resolved stay resolved; a row whose reference changed re-resolves."""
    imports = session.scalars(
        select(Import).where(Import.import_source_id == source.id, Import.status == "staged")
    )
    for imp in imports:
        try:
            parsed = importer.parse(imp.content, source.config, imp.file_name)
        except ImporterError as problem:
            raise _rejected(str(problem)) from None
        by_no = {r.row_no: r for r in parsed.rows}
        for staged in session.scalars(select(StagedRow).where(StagedRow.import_id == imp.id)):
            row = by_no.get(staged.row_no)
            if row is None:
                continue
            same_ref = staged.instrument_ref == row.instrument_ref
            staged.record = row.to_record()
            staged.instrument_ref = row.instrument_ref
            if not row.instrument_ref:
                staged.instrument_id = None
            elif not same_ref or staged.instrument_id is None:
                staged.instrument_id = _resolve_instrument(
                    session, row.instrument_ref_scheme, row.instrument_ref
                )
    session.flush()


def map_type(
    session: Session, context: AuditContext, import_id: int, raw_type: str, kind: str
) -> None:
    """Say what an unrecognised Type means for this Import's source. The meaning is saved in the
    source ``config`` (validated, audited) and applied to every staged Import of the source."""
    imp = get_import(session, import_id)
    if imp.status != "staged":
        raise ValidationError([FieldError("status", f"This Import is {imp.status}.")])
    wanted = raw_type.casefold()
    waiting = next(
        (u for u in unmapped_types(session, import_id) if u.raw_type.casefold() == wanted), None
    )
    if waiting is None:
        raise NotFoundError("That transaction type is not waiting to be mapped in this Import.")
    if kind not in waiting.choices:
        raise ValidationError([FieldError("kind", f"Choose one of: {', '.join(waiting.choices)}.")])
    source = session.get(ImportSource, imp.import_source_id)
    importer = registry.get(source.importer_key) if source else None
    if source is None or importer is None:
        raise NotFoundError(f"Import {import_id} has lost its Import source.")
    before = copy.deepcopy(source.config)
    config = copy.deepcopy(before)
    config.setdefault("type_map", {}).setdefault(imp.stream, {})[waiting.raw_type] = kind
    source.config = _validated_config(importer, config)
    session.flush()
    audit.record(
        session,
        context,
        entity_type="import_source",
        entity_id=source.id,
        action="update",
        before={"id": source.id, "config": before},
        after={"id": source.id, "config": source.config},
        summary=f"Mapped {imp.stream} type {waiting.raw_type} to {kind} for {importer.name}",
    )
    _reapply(session, source, importer)


def _re_resolve(session: Session, scheme: str, ref: str) -> None:
    """Re-run the crosswalk for every still-unresolved staged row naming this reference."""
    norm = normalise_identifier(ref)
    rows = session.scalars(
        select(StagedRow)
        .join(Import, Import.id == StagedRow.import_id)
        .where(
            Import.status == "staged",
            StagedRow.instrument_ref.is_not(None),
            StagedRow.instrument_id.is_(None),
        )
    )
    for row in rows:
        if _scheme_of(row) == scheme and normalise_identifier(row.instrument_ref or "") == norm:
            row.instrument_id = _resolve_instrument(session, scheme, row.instrument_ref)
    session.flush()


def _require_unresolved(
    session: Session, import_id: int, scheme: str, ref: str
) -> UnresolvedReference:
    """The waiting reference this names (as the file spelt it), or an error."""
    imp = get_import(session, import_id)
    if imp.status != "staged":
        raise ValidationError([FieldError("status", f"This Import is {imp.status}.")])
    norm = normalise_identifier(ref)
    for waiting in unresolved_references(session, import_id):
        if waiting.scheme == scheme and normalise_identifier(waiting.ref) == norm:
            return waiting
    raise NotFoundError("That reference is not waiting to be matched in this Import.")


def map_reference(
    session: Session,
    context: AuditContext,
    import_id: int,
    scheme: str,
    ref: str,
    instrument_id: int,
) -> None:
    """Map an unresolved reference to an existing Instrument: adds an audited identifier, then
    the rows re-resolve."""
    waiting = _require_unresolved(session, import_id, scheme, ref)
    reference_instruments.add_identifier(
        session, context, instrument_id, reference_instruments.IdentifierInput(scheme, waiting.ref)
    )
    _re_resolve(session, scheme, ref)


def create_for_reference(
    session: Session,
    context: AuditContext,
    import_id: int,
    scheme: str,
    ref: str,
    data: reference_instruments.InstrumentInput,
) -> Instrument:
    """Create an Instrument for an unresolved reference: it carries the reference as an identifier
    and, when the file gave a name, that name as an alias; then the rows re-resolve."""
    entry = _require_unresolved(session, import_id, scheme, ref)
    identifiers = [reference_instruments.IdentifierInput(scheme, entry.ref)]
    alias = entry.name
    if alias and scheme != "name" and reference_instruments.resolve(session, alias, "name") is None:
        identifiers.append(reference_instruments.IdentifierInput("name", alias))
    instrument = reference_instruments.create_instrument(
        session,
        context,
        reference_instruments.InstrumentInput(
            name=data.name,
            kind=data.kind,
            currency=data.currency,
            notes=data.notes,
            identifiers=tuple(identifiers),
        ),
    )
    _re_resolve(session, scheme, ref)
    return instrument


# --- staging -------------------------------------------------------------------------------


def _existing(session: Session, sha: str, source_id: int, stream: str) -> Import | None:
    return session.scalars(
        select(Import).where(
            Import.file_sha256 == sha,
            Import.import_source_id == source_id,
            Import.stream == stream,
            Import.status != "discarded",
        )
    ).first()


def _currencies(parsed: ParsedFile) -> set[str]:
    found: set[str] = set()
    for row in parsed.rows:
        found.update(m.currency for m in (row.fee, row.amount) if m is not None)
        if row.unit_price_ccy:
            found.add(row.unit_price_ccy)
    return found


def existing_for(session: Session, detection: Detection, account_id: int) -> Import | None:
    """The live Import of this exact file for the Account, if any. Read-only: creates nothing."""
    source = session.scalars(
        select(ImportSource).where(
            ImportSource.account_id == account_id,
            ImportSource.importer_key == detection.importer.key,
        )
    ).first()
    if source is None:
        return None
    return _existing(session, detection.sha256, source.id, detection.parsed.stream)


def stage(
    session: Session,
    context: AuditContext,
    content: bytes,
    file_name: str,
    account_id: int,
    *,
    save_identifier: bool = False,
) -> StageResult:
    """Stage a file against the Account the owner chose.

    The same file (hash, Import source, Stream) returns the existing Import and writes nothing.
    ``save_identifier`` records the file's account reference against the Account for next time.
    """
    detection = detect(session, content, file_name)
    parsed, importer = detection.parsed, detection.importer
    account = session.get(Account, account_id)
    if account is None:
        raise NotFoundError(f"Account {account_id} does not exist.")
    if account.closed_on is not None:
        raise ValidationError([FieldError("account_id", "This Account is closed.")])
    wrong = _currencies(parsed) - {account.currency}
    if wrong:
        raise ValidationError(
            [
                FieldError(
                    "account_id",
                    f"The file is in {', '.join(sorted(wrong))}, "
                    f"but this Account is in {account.currency}.",
                )
            ]
        )
    name = _display_name(file_name, parsed.account_reference)

    source = _source_for(session, context, account, importer)
    existing = _existing(session, detection.sha256, source.id, parsed.stream)
    if existing is not None:
        return StageResult(existing, False)
    if source.config.get("type_map"):  # Types the owner already mapped for this source apply now
        try:
            parsed = importer.parse(content, source.config, file_name)
        except ImporterError as problem:
            raise _rejected(str(problem)) from None

    imp = Import(
        import_source_id=source.id,
        stream=parsed.stream,
        file_sha256=detection.sha256,
        file_name=name,
        content=content,
        coverage_from=parsed.coverage_from,
        coverage_to=parsed.coverage_to,
        status="staged",
        warnings=list(parsed.warnings),
        added_at=_now(),
    )
    try:
        with session.begin_nested():
            session.add(imp)
    except IntegrityError:  # a concurrent request for the same file won: return its Import
        winner = _existing(session, detection.sha256, source.id, parsed.stream)
        if winner is None:
            raise
        return StageResult(winner, False)

    cache: dict[tuple[str | None, str], int | None] = {}
    for row in parsed.rows:
        key = (row.instrument_ref_scheme, normalise_identifier(row.instrument_ref or ""))
        if key not in cache:
            cache[key] = _resolve_instrument(session, row.instrument_ref_scheme, row.instrument_ref)
        session.add(
            StagedRow(
                import_id=imp.id,
                row_no=row.row_no,
                record=row.to_record(),
                instrument_ref=row.instrument_ref,
                instrument_id=cache[key] if row.instrument_ref else None,
            )
        )
    session.flush()
    audit.record(
        session,
        context,
        entity_type="import",
        entity_id=imp.id,
        action="create",
        after=_snapshot(imp, len(parsed.rows)),
        summary=f"Staged {parsed.stream} file ({len(parsed.rows)} rows) for {account.name}",
    )
    if save_identifier and parsed.account_reference and account not in detection.accounts:
        ref = parsed.account_reference
        account_identifiers.add_identifier(
            session,
            context,
            account.id,
            account_identifiers.IdentifierInput(ref.kind, ref.value, ref.institution),
        )
    return StageResult(imp, True)


def _snapshot(imp: Import, rows: int) -> dict[str, Any]:
    return {
        "id": imp.id,
        "import_source_id": imp.import_source_id,
        "stream": imp.stream,
        "file_name": imp.file_name,
        "coverage_from": imp.coverage_from.isoformat(),
        "coverage_to": imp.coverage_to.isoformat(),
        "status": imp.status,
        "rows": rows,
    }


def discard(session: Session, context: AuditContext, import_id: int) -> Import:
    """Mark a staged Import discarded. Its rows stay; the same file can then be staged again."""
    imp = get_import(session, import_id)
    if imp.status != "staged":
        raise ValidationError(
            [
                FieldError(
                    "status", f"Only a staged Import can be discarded; this one is {imp.status}."
                )
            ]
        )
    rows = row_count(session, import_id)
    before = _snapshot(imp, rows)
    imp.status = "discarded"
    session.flush()
    audit.record(
        session,
        context,
        entity_type="import",
        entity_id=imp.id,
        action="update",
        before=before,
        after=_snapshot(imp, rows),
        summary=f"Discarded {imp.stream} file {imp.file_name}",
    )
    return imp


# --- reads ---------------------------------------------------------------------------------


def get_import(session: Session, import_id: int) -> Import:
    imp = session.get(Import, import_id)
    if imp is None:
        raise NotFoundError(f"Import {import_id} does not exist.")
    return imp


def row_count(session: Session, import_id: int) -> int:
    return (
        session.scalar(
            select(func.count()).select_from(StagedRow).where(StagedRow.import_id == import_id)
        )
        or 0
    )


def summarise(session: Session, imp: Import) -> ImportSummary:
    source = session.get(ImportSource, imp.import_source_id)
    account = session.get(Account, source.account_id) if source else None
    if source is None or account is None:
        raise NotFoundError(f"Import {imp.id} has lost its Import source or Account.")
    importer = registry.get(source.importer_key)
    unresolved = session.scalar(
        select(func.count())
        .select_from(StagedRow)
        .where(
            StagedRow.import_id == imp.id,
            StagedRow.instrument_ref.is_not(None),
            StagedRow.instrument_id.is_(None),
        )
    )
    unmapped = session.scalar(
        select(func.count())
        .select_from(StagedRow)
        .where(
            StagedRow.import_id == imp.id,
            StagedRow.record["kind"].as_string() == UNRECOGNISED,
        )
    )
    return ImportSummary(
        import_=imp,
        account=account,
        importer_name=importer.name if importer else source.importer_key,
        row_count=row_count(session, imp.id),
        unresolved_count=unresolved or 0,
        unmapped_count=unmapped or 0,
    )


def list_imports(session: Session) -> list[ImportSummary]:
    imports = session.scalars(select(Import).order_by(Import.added_at.desc(), Import.id.desc()))
    return [summarise(session, i) for i in imports]


def list_rows(session: Session, import_id: int, offset: int, limit: int) -> list[StagedRow]:
    get_import(session, import_id)
    return list(
        session.scalars(
            select(StagedRow)
            .where(StagedRow.import_id == import_id)
            .order_by(StagedRow.row_no)
            .offset(offset)
            .limit(limit)
        )
    )
