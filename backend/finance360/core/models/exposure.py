"""Exposure vocabulary and Exposure profiles (ADR-0030 sections 2 and 3).

Services: ``capabilities/reference`` (``exposure_profiles``). Dimensions and Exposure labels are
seeded data (``core/exposure_vocab.py``). An ``exposure_profile`` is a ``SupersessionMixin`` row
that is never updated or deleted; its ``exposure_weight`` children are immutable (a correction
writes new ones). Weights are integer parts per million (a ``Weight``), never a float. Triggers in
the migration enforce all of this. An ``exposure_reference`` (fund-of-funds, ADR-0030 section 5) is
a weighted pointer from a profile to another Instrument, immutable like a weight.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from finance360.core.db import Base
from finance360.core.models.supersession import SupersessionMixin


class ExposureDimension(Base):
    __tablename__ = "exposure_dimension"

    key: Mapped[str] = mapped_column(String(30), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    position: Mapped[int] = mapped_column(Integer)


class ExposureLabel(Base):
    __tablename__ = "exposure_label"
    __table_args__ = (
        UniqueConstraint("dimension_key", "key", name="uq_exposure_label_dimension_key_label"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    dimension_key: Mapped[str] = mapped_column(ForeignKey("exposure_dimension.key"))
    key: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(100))
    #: Seed order: the order lines are shown in, and the tie-break when an excess is trimmed.
    position: Mapped[int] = mapped_column(Integer)


class ExposureProfile(SupersessionMixin, Base):
    __tablename__ = "exposure_profile"
    __table_args__ = (
        CheckConstraint("length(trim(coalesce(provenance_note, ''))) > 0", name="note_required"),
        CheckConstraint("age_from IS NULL OR age_from >= 0", name="age_from_not_negative"),
        # One current profile per (Instrument, as_at, age_from): two partial indexes, because
        # SQLite treats NULLs as distinct in a unique index.
        Index(
            "uq_exposure_profile_current",
            "instrument_id",
            "as_at",
            unique=True,
            sqlite_where=text("superseded_at IS NULL AND age_from IS NULL"),
        ),
        Index(
            "uq_exposure_profile_current_banded",
            "instrument_id",
            "as_at",
            "age_from",
            unique=True,
            sqlite_where=text("superseded_at IS NULL AND age_from IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instrument.id"), index=True)
    #: The date the published composition is as at.
    as_at: Mapped[date] = mapped_column(Date)
    #: A glide-path band (#6); null for an ordinary profile.
    age_from: Mapped[int | None] = mapped_column(Integer)
    #: ``manual``, a registered Provider key, or ``ai`` (AI-proposed, owner-confirmed).
    source: Mapped[str] = mapped_column(String(20))
    retrieved_on: Mapped[date] = mapped_column(Date)
    provenance_note: Mapped[str | None] = mapped_column(Text)

    weights: Mapped[list[ExposureWeight]] = relationship(
        order_by="ExposureWeight.id", lazy="selectin"
    )
    references: Mapped[list[ExposureReference]] = relationship(
        order_by="ExposureReference.id", lazy="selectin"
    )


class ExposureWeight(Base):
    __tablename__ = "exposure_weight"
    __table_args__ = (
        CheckConstraint("weight_ppm > 0 AND weight_ppm <= 1000000", name="weight_range"),
        ForeignKeyConstraint(
            ["dimension_key", "label_key"],
            ["exposure_label.dimension_key", "exposure_label.key"],
            name="fk_exposure_weight_label",
        ),
        UniqueConstraint(
            "profile_id", "dimension_key", "label_key", name="uq_exposure_weight_profile_label"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("exposure_profile.id"), index=True)
    dimension_key: Mapped[str] = mapped_column(String(30))
    label_key: Mapped[str] = mapped_column(String(50))
    #: Exact parts per million of 1 (a ``Weight``).
    weight_ppm: Mapped[int] = mapped_column(Integer)


class ExposureReference(Base):
    """A fund-of-funds reference: part of a profile is the Instrument ``instrument_id``, resolved
    through that Instrument's own profile on the look-through date (never a pinned version)."""

    __tablename__ = "exposure_reference"
    __table_args__ = (
        CheckConstraint("weight_ppm > 0 AND weight_ppm <= 1000000", name="weight_range"),
        UniqueConstraint(
            "profile_id", "instrument_id", name="uq_exposure_reference_profile_instrument"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("exposure_profile.id"), index=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instrument.id"), index=True)
    #: Exact parts per million of 1 (a ``Weight``).
    weight_ppm: Mapped[int] = mapped_column(Integer)
