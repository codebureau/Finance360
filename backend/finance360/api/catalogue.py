"""Read-only Category and Account type catalogue. Thin: grouping lives in ``capabilities``."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from finance360.api.household import get_session
from finance360.capabilities.position import catalogue

router = APIRouter(prefix="/api/catalogue", tags=["catalogue"])


class AccountTypeOut(BaseModel):
    key: str
    name: str
    behaviour_key: str


class CategoryOut(BaseModel):
    key: str
    name: str
    account_types: list[AccountTypeOut]


class ClassOut(BaseModel):
    class_: str = Field(serialization_alias="class", description="asset or liability")
    categories: list[CategoryOut]


class CatalogueOut(BaseModel):
    version: int
    classes: list[ClassOut]


SessionDep = Annotated[Session, Depends(get_session, scope="function")]


@router.get("", response_model=CatalogueOut, operation_id="getCatalogue")
def get_catalogue(session: SessionDep) -> CatalogueOut:
    view = catalogue.get_catalogue(session)
    return CatalogueOut(
        version=view.version,
        classes=[
            ClassOut(
                class_=c.class_,
                categories=[
                    CategoryOut(
                        key=cat.key,
                        name=cat.name,
                        account_types=[
                            AccountTypeOut(key=t.key, name=t.name, behaviour_key=t.behaviour_key)
                            for t in cat.account_types
                        ],
                    )
                    for cat in c.categories
                ],
            )
            for c in view.classes
        ],
    )
