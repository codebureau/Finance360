"""The seeded Exposure vocabulary and its idempotent startup seeder (ADR-0030 section 2).

Same shape as the catalogue seeder: it inserts Dimensions and Exposure labels whose key is missing
and never touches an existing row. A new label is a seed change (bump
``EXPOSURE_VOCAB_SEED_VERSION``), not code or a migration. #83 seeds ``asset_class`` only; #84
adds the other three Dimensions. Seeding is not audited (system bootstrap); the version is
recorded instead.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from finance360.core.exposure import ASSET_CLASS
from finance360.core.models.catalogue import Meta
from finance360.core.models.exposure import ExposureDimension, ExposureLabel

EXPOSURE_VOCAB_SEED_VERSION = 1
EXPOSURE_VOCAB_VERSION_KEY = "exposure_vocab_seed_version"


@dataclass(frozen=True)
class SeedDimension:
    key: str
    name: str
    #: ``(key, display name)`` in seed order. Every Dimension ends with ``other``.
    labels: tuple[tuple[str, str], ...]


#: The broad classes as Australian funds publish them, Australian and international shares apart.
DIMENSIONS = (
    SeedDimension(
        ASSET_CLASS,
        "Asset class",
        (
            ("australian_shares", "Australian shares"),
            ("international_shares", "International shares"),
            ("property", "Property"),
            ("infrastructure", "Infrastructure"),
            ("fixed_income", "Fixed income"),
            ("cash", "Cash"),
            ("alternatives", "Alternatives"),
            ("other", "Other"),
        ),
    ),
)


def seed_exposure_vocab(session: Session) -> None:
    """Insert any missing Dimension and label; never modify an existing row."""
    known = {d.key for d in session.scalars(select(ExposureDimension))}
    for position, seed in enumerate(DIMENSIONS):
        if seed.key not in known:
            session.add(ExposureDimension(key=seed.key, name=seed.name, position=position))
    session.flush()
    have = set(session.execute(select(ExposureLabel.dimension_key, ExposureLabel.key)).all())
    for seed in DIMENSIONS:
        for position, (key, name) in enumerate(seed.labels):
            if (seed.key, key) not in have:
                session.add(
                    ExposureLabel(dimension_key=seed.key, key=key, name=name, position=position)
                )
    meta = session.get(Meta, EXPOSURE_VOCAB_VERSION_KEY)
    if meta is None:
        session.add(Meta(key=EXPOSURE_VOCAB_VERSION_KEY, value=str(EXPOSURE_VOCAB_SEED_VERSION)))
    else:
        meta.value = str(EXPOSURE_VOCAB_SEED_VERSION)
    session.flush()
