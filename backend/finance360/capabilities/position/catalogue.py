"""Read the Category / Account type catalogue, grouped Class -> Category -> Account types."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from finance360.core.catalogue import CATALOGUE_VERSION_KEY
from finance360.core.models.catalogue import CLASSES, AccountType, Category, Meta


@dataclass(frozen=True)
class AccountTypeView:
    key: str
    name: str
    behaviour_key: str


@dataclass(frozen=True)
class CategoryView:
    key: str
    name: str
    account_types: list[AccountTypeView]


@dataclass(frozen=True)
class ClassView:
    class_: str
    categories: list[CategoryView]


@dataclass(frozen=True)
class CatalogueView:
    version: int
    classes: list[ClassView]


def get_catalogue(session: Session) -> CatalogueView:
    types_by_category: dict[int, list[AccountTypeView]] = {}
    for t in session.scalars(select(AccountType).order_by(AccountType.id)):
        types_by_category.setdefault(t.category_id, []).append(
            AccountTypeView(t.key, t.name, t.behaviour_key)
        )
    grouped: dict[str, list[CategoryView]] = {c: [] for c in CLASSES}
    for c in session.scalars(select(Category).order_by(Category.id)):
        grouped[c.class_].append(CategoryView(c.key, c.name, types_by_category.get(c.id, [])))
    meta = session.get(Meta, CATALOGUE_VERSION_KEY)
    return CatalogueView(
        version=int(meta.value) if meta else 0,
        classes=[ClassView(cls, cats) for cls, cats in grouped.items() if cats],
    )
