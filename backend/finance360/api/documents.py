"""``POST /api/documents/read``: read a document against a Document profile (ADR-0024).

Read-only. It loads, matches and checks in memory and returns a Reading: it stores nothing, writes
no audit row and logs nothing from the document. The file travels as the raw request body (as
Evidence does), its Content-Type ``application/pdf`` or ``text/plain``. Amounts in the response are
plain AUD decimal strings; the filled figure is a suggestion the person confirms on the form.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from finance360.api.household import ERRORS
from finance360.api.uploads import FileDep
from finance360.capabilities.documents import service
from finance360.capabilities.documents.reading import Reading
from finance360.core.errors import FieldError, ValidationError
from finance360.core.money import Money, format_amount

router = APIRouter(prefix="/api/documents", tags=["documents"])

#: The media types a document may be read from.
FILE_BODY: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            "application/pdf": {"schema": {"type": "string", "format": "binary"}},
            "text/plain": {"schema": {"type": "string", "format": "binary"}},
        },
    }
}


class PartOut(BaseModel):
    page: int = Field(description="The page the row was read from.")
    value: str = Field(description="The row's amount as a plain AUD decimal string, or its text.")


class FoundOut(BaseModel):
    key: str = Field(description="The target form's field name.")
    value: str = Field(
        description="Money as a plain AUD decimal string, text as text, a period as the year it "
        "ends in."
    )
    kind: str = Field(description="'money', 'text' or 'fy'.")
    page: int = Field(description="The page it was read from; 0 when the line was absent.")
    how: str = Field(description="'read' (beside its label) or 'absent' (omitted, taken as 0).")
    parts: list[PartOut] = Field(
        description="Every source row, in reading order, when the figure adds up repeated rows "
        "(such as one per leave payment); empty for a figure read from one row."
    )


class NotFoundOut(BaseModel):
    key: str
    reason: str = Field(
        description="'label_not_found', 'ambiguous', 'no_value', 'section_not_found', "
        "'not_an_amount', 'not_a_year', 'negative' or 'too_long'."
    )
    required: bool


class CheckOut(BaseModel):
    kind: str = Field(description="'sum' or 'period'.")
    status: str = Field(description="'agree', 'disagree' or 'skipped'.")
    fields: list[str] = Field(description="The total first, then its parts.")
    message: str
    assumed_zero: list[str] = Field(
        description="Parts the statement did not state that the sum counted as zero."
    )


class ReadingOut(BaseModel):
    outcome: str = Field(description="'ok', 'warnings', 'needs_repair' or 'unrecognised'.")
    profile_key: str | None
    profile_version: int | None
    found: list[FoundOut]
    not_found: list[NotFoundOut]
    checks: list[CheckOut]


def _shown(value: Money | str | int) -> str:
    return format_amount(value) if isinstance(value, Money) else str(value)


def _found(item: service.Found) -> FoundOut:
    value = item.value
    kind = "money" if isinstance(value, Money) else "fy" if isinstance(value, int) else "text"
    return FoundOut(
        key=item.key,
        value=_shown(value),
        kind=kind,
        page=item.page,
        how=item.how,
        parts=[PartOut(page=p.page, value=_shown(p.value)) for p in item.parts],
    )


def reading_out(reading: Reading) -> ReadingOut:
    """A Reading as the API returns it (also used by the Document profile test routes)."""
    return ReadingOut(
        outcome=reading.outcome,
        profile_key=reading.profile_key,
        profile_version=reading.profile_version,
        found=[_found(f) for f in reading.found],
        not_found=[
            NotFoundOut(key=n.key, reason=n.reason, required=n.required) for n in reading.not_found
        ],
        checks=[
            CheckOut(
                kind=c.kind,
                status=c.status,
                fields=list(c.fields),
                message=c.message,
                assumed_zero=list(c.assumed_zero),
            )
            for c in reading.checks
        ],
    )


@router.post(
    "/read",
    response_model=ReadingOut,
    operation_id="readDocument",
    responses=ERRORS,  # type: ignore[arg-type]
    openapi_extra=FILE_BODY,
)
def read_document(
    request: Request,
    body: FileDep,
    target: Annotated[
        str, Query(description="The form the reading feeds, such as 'tax.income_statement'.")
    ],
    financial_year: Annotated[
        int | None,
        Query(description="The form's financial year (the year it ends in), to check the period."),
    ] = None,
) -> ReadingOut:
    media_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    try:
        if media_type == "application/pdf":
            reading = service.read_pdf(body, target, financial_year)
        elif media_type == "text/plain":
            reading = service.read_text(
                body.decode("utf-8", errors="replace"), target, financial_year
            )
        else:
            raise ValidationError([FieldError("file", "Read a PDF or a text file.")])
    except service.UnknownTarget:
        raise ValidationError(
            [FieldError("target", "No document can be read into that form.")]
        ) from None
    except service.LoadError as problem:
        raise ValidationError([FieldError("file", problem.message)]) from None
    return reading_out(reading)
