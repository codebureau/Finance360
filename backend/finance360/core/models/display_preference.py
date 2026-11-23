"""Display preference (ADR-0031 section 3): a presentation choice, kept per Login.

Presentation only: not audited, not a Household fact, not exposed over MCP. The key and value
shapes are validated by ``capabilities.preferences`` so nothing financial can be stored.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from finance360.core.db import Base


class DisplayPreference(Base):
    __tablename__ = "display_preference"
    __table_args__ = (
        CheckConstraint("length(key) BETWEEN 1 AND 64", name="key_length"),
        CheckConstraint("length(value) BETWEEN 1 AND 24", name="value_length"),
    )

    login_id: Mapped[int] = mapped_column(ForeignKey("login.id"), primary_key=True)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(24))
    #: UTC, stored naive like every timestamp here.
    updated_at: Mapped[datetime] = mapped_column(DateTime)
