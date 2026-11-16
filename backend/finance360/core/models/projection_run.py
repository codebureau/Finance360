"""Saved projection run (ADR-0016 section 4).

Services: ``capabilities/projection/runs.py``.

One immutable row per explicit "Save run". Migration 0017 adds triggers that reject UPDATE and
DELETE (as for ``audit_event``) and ``json_valid`` CHECKs on the payload columns. The payloads are
canonical JSON text (exact: money as ``{minor, ccy}``, rates as decimal strings, no floats), so no
Money or Rate sits in a relational column. The two ``funds_last_to_age`` / ``lasts_beyond_horizon``
columns are a denormalised headline for list views, which never read the payloads.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from finance360.core.db import Base

LABEL_MAX = 100


class ProjectionRun(Base):
    __tablename__ = "projection_run"
    __table_args__ = (
        CheckConstraint("json_valid(input_json)", name="input_json_valid"),
        CheckConstraint("json_valid(result_json)", name="result_json_valid"),
        CheckConstraint(
            "sustainable_json IS NULL OR json_valid(sustainable_json)",
            name="sustainable_json_valid",
        ),
        CheckConstraint("json_valid(provenance_json)", name="provenance_json_valid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    #: UTC, stored naive like every timestamp here.
    created_at: Mapped[datetime] = mapped_column(DateTime)
    label: Mapped[str | None] = mapped_column(String(LABEL_MAX))
    #: RESTRICT, not SET NULL: nulling it would be an UPDATE, and a run is never updated.
    scenario_id: Mapped[int] = mapped_column(ForeignKey("scenario.id", ondelete="RESTRICT"))
    #: The Scenario's name when the run was saved.
    scenario_name: Mapped[str] = mapped_column(String(100))
    engine_version: Mapped[int]
    input_version: Mapped[int]
    #: Null until the Rule pack work (A4) owns its form.
    rule_pack_version: Mapped[str | None] = mapped_column(String(50))
    input_json: Mapped[str] = mapped_column(Text)
    result_json: Mapped[str] = mapped_column(Text)
    sustainable_json: Mapped[str | None] = mapped_column(Text)
    provenance_json: Mapped[str] = mapped_column(Text)
    funds_last_to_age: Mapped[int | None]
    lasts_beyond_horizon: Mapped[bool]
