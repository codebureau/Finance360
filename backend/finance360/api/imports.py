"""Import endpoints (ADR-0013 section 5). Thin: every rule lives in ``capabilities.imports``.

A file travels as the raw request body (``application/octet-stream``), not as multipart form data:
nothing else in the API needs a form parser. ``POST /detect`` is read-only and names the Importer,
the Stream, the Coverage and the Account the file's reference resolves to; ``POST`` stages the
file against the Account the owner confirmed. No response carries an account reference unmasked.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from finance360.api.household import ERRORS, ContextDep, SessionDep
from finance360.api.transactions import MoneyOut, TransactionsOut, transaction_out
from finance360.capabilities.imports import commit, service, transactions
from finance360.capabilities.imports.importer import IGNORED
from finance360.capabilities.reference import fetch as reference_fetch
from finance360.capabilities.reference import instruments as reference
from finance360.core.account_identifier import mask_value
from finance360.core.errors import FieldError, ValidationError
from finance360.core.models.imports import Import, StagedRow
from finance360.core.models.instrument import Instrument
from finance360.core.money import Money, format_amount

router = APIRouter(prefix="/api/imports", tags=["imports"])

#: How the request body is described to the generated client (no multipart parser is installed).
FILE_BODY: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
    }
}


async def _file_body(request: Request) -> bytes:
    """The raw body, refused as soon as it passes the size limit."""
    limit = service.MAX_FILE_BYTES
    too_big = ValidationError(
        [FieldError("file", f"The file is larger than {limit // 1_000_000} MB.")]
    )
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise too_big
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > limit:
            raise too_big
    return bytes(data)


FileDep = Annotated[bytes, Depends(_file_body)]


class AccountRef(BaseModel):
    id: int
    name: str


class DetectOut(BaseModel):
    importer_key: str
    importer_name: str
    stream: str
    coverage_from: date
    coverage_to: date
    row_count: int
    warnings: list[str]
    #: The file's account reference, **masked**. ``None`` when the file has none.
    reference_masked: str | None
    #: The Accounts that reference resolves to: one means preselect it; none or several mean the
    #: owner picks.
    matches: list[AccountRef]
    #: An Import of this exact file already staged or committed for the preselected Account.
    duplicate_import_id: int | None


class UnresolvedOut(BaseModel):
    scheme: str
    ref: str
    name: str | None
    kind_hint: str | None
    row_count: int


class UnmappedTypeOut(BaseModel):
    """A transaction type the Importer does not know: the owner says what it means."""

    raw_type: str
    row_count: int
    #: What it may be mapped to, for this Stream.
    choices: list[str]


class ImportOut(BaseModel):
    id: int
    stream: str
    file_name: str
    status: str
    coverage_from: date
    coverage_to: date
    added_at: datetime
    account_id: int
    account_name: str
    importer_name: str
    row_count: int
    unresolved_count: int
    #: Staged rows whose transaction type is not mapped yet.
    unmapped_count: int
    #: Commit is refused while any reference is unresolved or any transaction type is unmapped.
    commit_blocked: bool
    warnings: list[str]


class CommittedOut(BaseModel):
    """What a committed Import did, read back from the Transactions it wrote and replaced."""

    committed_at: datetime
    added: int
    replaced: int
    unchanged: int
    #: Instruments this Import brought in that have no Price yet.
    instruments_without_price: int
    #: True when Fetch now is worth offering: such Instruments exist and a Price provider is set.
    offer_fetch: bool


class ImportDetailOut(ImportOut):
    unresolved: list[UnresolvedOut]
    unmapped_types: list[UnmappedTypeOut]
    #: Set once the Import is committed.
    committed: CommittedOut | None


class StageOut(ImportDetailOut):
    #: False: the same file was already imported for this Account, and nothing changed.
    created: bool


class StagedRowOut(BaseModel):
    row_no: int
    occurred_on: date
    kind: str
    instrument_ref: str | None
    instrument_name: str | None
    instrument_id: int | None
    resolved_name: str | None
    quantity: str | None
    unit_price: str | None
    unit_price_ccy: str | None
    fee: MoneyOut | None
    amount: MoneyOut | None
    description: str | None
    #: For a staged Import: ``added`` (becomes a Transaction) or ``unchanged`` (an equal current
    #: Transaction already exists). ``None`` once the Import is committed or discarded.
    change: str | None


class RowsOut(BaseModel):
    total: int
    items: list[StagedRowOut]


class WarningOut(BaseModel):
    code: str
    message: str


class DiffOut(BaseModel):
    """What committing a staged Import would do. Warnings never block commit."""

    scope_from: date = Field(description="The range compared: Coverage widened to the file's rows.")
    scope_to: date
    added: int
    removed: int
    unchanged: int
    warnings: list[WarningOut]
    commit_blocked: bool


class MapIn(BaseModel):
    scheme: str = ""
    ref: str = ""
    instrument_id: int | None = None


class MapTypeIn(BaseModel):
    raw_type: str = ""
    kind: str = ""


class CreateIn(BaseModel):
    scheme: str = ""
    ref: str = ""
    name: str = ""
    kind: str = ""
    currency: str = ""
    notes: str | None = None


def _money(raw: Any) -> MoneyOut | None:
    if not isinstance(raw, dict):
        return None
    fields = cast("dict[str, Any]", raw)
    money = Money(int(fields["minor"]), str(fields["ccy"]))
    return MoneyOut(
        amount=format_amount(money), amount_minor=money.amount_minor, currency=money.currency
    )


def _import_out(session: Session, imp: Import) -> ImportOut:
    s = service.summarise(session, imp)
    return ImportOut(
        id=imp.id,
        stream=imp.stream,
        file_name=imp.file_name,
        status=imp.status,
        coverage_from=imp.coverage_from,
        coverage_to=imp.coverage_to,
        added_at=imp.added_at.replace(tzinfo=UTC),
        account_id=s.account.id,
        account_name=s.account.name,
        importer_name=s.importer_name,
        row_count=s.row_count,
        unresolved_count=s.unresolved_count,
        unmapped_count=s.unmapped_count,
        commit_blocked=imp.status == "staged" and (s.unresolved_count + s.unmapped_count) > 0,
        warnings=list(imp.warnings),
    )


def _price_provider(request: Request) -> str | None:
    return request.app.state.settings.price_provider


PriceProviderDep = Annotated[str | None, Depends(_price_provider)]


def _detail(session: Session, imp: Import, price_provider: str | None = None) -> ImportDetailOut:
    unresolved = [
        UnresolvedOut(
            scheme=u.scheme, ref=u.ref, name=u.name, kind_hint=u.kind_hint, row_count=u.row_count
        )
        for u in service.unresolved_references(session, imp.id)
    ]
    unmapped = [
        UnmappedTypeOut(raw_type=u.raw_type, row_count=u.row_count, choices=list(u.choices))
        for u in service.unmapped_types(session, imp.id)
    ]
    summary = commit.committed_summary(session, imp)
    missing = reference_fetch.instruments_without_price(session, imp.id) if summary else 0
    committed = (
        CommittedOut(
            committed_at=(imp.committed_at or imp.added_at).replace(tzinfo=UTC),
            added=summary.added,
            replaced=summary.replaced,
            unchanged=summary.unchanged,
            instruments_without_price=missing,
            offer_fetch=missing > 0 and price_provider is not None,
        )
        if summary
        else None
    )
    return ImportDetailOut(
        **_import_out(session, imp).model_dump(),
        unresolved=unresolved,
        unmapped_types=unmapped,
        committed=committed,
    )


def _row_out(row: StagedRow, instruments: dict[int, str], change: str | None) -> StagedRowOut:
    rec = row.record
    return StagedRowOut(
        row_no=row.row_no,
        occurred_on=date.fromisoformat(rec["occurred_on"]),
        kind=rec["kind"],
        instrument_ref=row.instrument_ref,
        instrument_name=rec.get("instrument_name"),
        instrument_id=row.instrument_id,
        resolved_name=instruments.get(row.instrument_id) if row.instrument_id else None,
        quantity=rec.get("quantity"),
        unit_price=rec.get("unit_price"),
        unit_price_ccy=rec.get("unit_price_ccy"),
        fee=_money(rec.get("fee")),
        amount=_money(rec.get("amount")),
        description=rec.get("description"),
        change=change,
    )


@router.post(
    "/detect",
    response_model=DetectOut,
    operation_id="detectImport",
    responses=ERRORS,  # type: ignore[arg-type]
    openapi_extra=FILE_BODY,
)
def detect_import(body: FileDep, session: SessionDep, file_name: str = "") -> DetectOut:
    """Read a file without storing it: which Importer, which Stream and Coverage, which Account."""
    found = service.detect(session, body, file_name)
    duplicate = (
        service.existing_for(session, found, found.accounts[0].id)
        if len(found.accounts) == 1
        else None
    )
    ref = found.parsed.account_reference
    return DetectOut(
        importer_key=found.importer.key,
        importer_name=found.importer.name,
        stream=found.parsed.stream,
        coverage_from=found.parsed.coverage_from,
        coverage_to=found.parsed.coverage_to,
        row_count=len(found.parsed.rows),
        warnings=list(found.parsed.warnings),
        reference_masked=mask_value(ref.value) if ref else None,
        matches=[AccountRef(id=a.id, name=a.name) for a in found.accounts],
        duplicate_import_id=duplicate.id if duplicate else None,
    )


@router.post(
    "",
    response_model=StageOut,
    status_code=201,
    operation_id="stageImport",
    responses={**ERRORS, 200: {"model": StageOut}},  # type: ignore[arg-type]
    openapi_extra=FILE_BODY,
)
def stage_import(
    body: FileDep,
    session: SessionDep,
    ctx: ContextDep,
    response: Response,
    provider: PriceProviderDep,
    account_id: int,
    file_name: str = "",
    save_identifier: bool = False,
) -> StageOut:
    """Stage a file against the chosen Account. The same file again answers 200 with the existing
    Import and changes nothing."""
    result = service.stage(
        session, ctx, body, file_name, account_id, save_identifier=save_identifier
    )
    if not result.created:
        response.status_code = 200
    return StageOut(
        **_detail(session, result.import_, provider).model_dump(), created=result.created
    )


@router.get("", response_model=list[ImportOut], operation_id="listImports")
def list_imports(session: SessionDep) -> list[ImportOut]:
    return [_import_out(session, s.import_) for s in service.list_imports(session)]


@router.get(
    "/{import_id}",
    response_model=ImportDetailOut,
    operation_id="getImport",
    responses=ERRORS,  # type: ignore[arg-type]
)
def get_import(import_id: int, session: SessionDep, provider: PriceProviderDep) -> ImportDetailOut:
    return _detail(session, service.get_import(session, import_id), provider)


@router.get(
    "/{import_id}/rows",
    response_model=RowsOut,
    operation_id="listImportRows",
    responses=ERRORS,  # type: ignore[arg-type]
)
def list_rows(import_id: int, session: SessionDep, offset: int = 0, limit: int = 25) -> RowsOut:
    limit = max(1, min(limit, 200))
    rows = service.list_rows(session, import_id, max(0, offset), limit)
    ids = {r.instrument_id for r in rows if r.instrument_id}
    names = {i: n for i in ids if (n := _instrument_name(session, i))}
    added: set[int] | None = None
    if service.get_import(session, import_id).status == "staged":
        added = {r.row_no for r in commit.preview(session, import_id).added}

    def change_of(row: StagedRow) -> str | None:
        if added is None or row.record["kind"] == IGNORED:
            return None
        return "added" if row.row_no in added else "unchanged"

    return RowsOut(
        total=service.row_count(session, import_id),
        items=[_row_out(r, names, change_of(r)) for r in rows],
    )


def _instrument_name(session: Session, instrument_id: int) -> str | None:
    found = session.get(Instrument, instrument_id)
    return found.name if found else None


@router.post(
    "/{import_id}/discard",
    response_model=ImportDetailOut,
    operation_id="discardImport",
    responses=ERRORS,  # type: ignore[arg-type]
)
def discard_import(import_id: int, session: SessionDep, ctx: ContextDep) -> ImportDetailOut:
    return _detail(session, service.discard(session, ctx, import_id))


@router.post(
    "/{import_id}/references/map",
    response_model=ImportDetailOut,
    operation_id="mapImportReference",
    responses=ERRORS,  # type: ignore[arg-type]
)
def map_reference(
    import_id: int, body: MapIn, session: SessionDep, ctx: ContextDep
) -> ImportDetailOut:
    """Map an unresolved reference to an existing Instrument. Adds an audited identifier
    to it, then the staged rows re-resolve."""
    if body.instrument_id is None:
        raise ValidationError([FieldError("instrument_id", "Choose an Instrument.")])
    service.map_reference(session, ctx, import_id, body.scheme, body.ref, body.instrument_id)
    return _detail(session, service.get_import(session, import_id))


@router.post(
    "/{import_id}/references/create",
    response_model=ImportDetailOut,
    operation_id="createImportReferenceInstrument",
    responses=ERRORS,  # type: ignore[arg-type]
)
def create_reference_instrument(
    import_id: int, body: CreateIn, session: SessionDep, ctx: ContextDep
) -> ImportDetailOut:
    """Create an Instrument for an unresolved reference; the staged rows re-resolve."""
    service.create_for_reference(
        session,
        ctx,
        import_id,
        body.scheme,
        body.ref,
        reference.InstrumentInput(
            name=body.name, kind=body.kind, currency=body.currency, notes=body.notes
        ),
    )
    return _detail(session, service.get_import(session, import_id))


@router.post(
    "/{import_id}/types/map",
    response_model=ImportDetailOut,
    operation_id="mapImportType",
    responses=ERRORS,  # type: ignore[arg-type]
)
def map_type(
    import_id: int, body: MapTypeIn, session: SessionDep, ctx: ContextDep
) -> ImportDetailOut:
    """Say what an unrecognised transaction type means. It is saved on the Import source (audited)
    and applied to the staged rows of every staged Import of that source."""
    service.map_type(session, ctx, import_id, body.raw_type, body.kind)
    return _detail(session, service.get_import(session, import_id))


@router.get(
    "/{import_id}/diff",
    response_model=DiffOut,
    operation_id="getImportDiff",
    responses=ERRORS,  # type: ignore[arg-type]
)
def get_diff(import_id: int, session: SessionDep) -> DiffOut:
    """What committing would do: rows added, removed and unchanged, plus reconciliation warnings.
    Only a staged Import has a diff."""
    plan = commit.preview(session, import_id)
    blocked = bool(
        service.unresolved_references(session, import_id)
        or service.unmapped_types(session, import_id)
    )
    return DiffOut(
        scope_from=plan.scope_from,
        scope_to=plan.scope_to,
        added=len(plan.added),
        removed=len(plan.removed),
        unchanged=len(plan.unchanged),
        warnings=[WarningOut(code=w.code, message=w.message) for w in plan.warnings],
        commit_blocked=blocked,
    )


@router.get(
    "/{import_id}/diff/removed",
    response_model=TransactionsOut,
    operation_id="listImportRemoved",
    responses=ERRORS,  # type: ignore[arg-type]
)
def list_removed(
    import_id: int, session: SessionDep, offset: int = 0, limit: int = 10
) -> TransactionsOut:
    """The current Transactions committing would supersede (they stay retrievable)."""
    page = transactions.list_removed(session, import_id, max(0, offset), max(1, min(limit, 200)))
    return TransactionsOut(
        total=page.total,
        items=[transaction_out(r) for r in page.items],
    )


@router.post(
    "/{import_id}/commit",
    response_model=ImportDetailOut,
    operation_id="commitImport",
    responses=ERRORS,  # type: ignore[arg-type]
)
def commit_import(
    import_id: int, session: SessionDep, ctx: ContextDep, provider: PriceProviderDep
) -> ImportDetailOut:
    """Commit a staged Import: its rows become Transactions, rows it replaces are superseded, one
    audit event. Refused while any reference is unmatched or any transaction type unmapped, or if
    it is already committed."""
    commit.commit(session, ctx, import_id)
    return _detail(session, service.get_import(session, import_id), provider)
