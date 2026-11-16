"""Where a price backfill starts: the earliest current trade date per Instrument (ADR-0013 s6).

The first fetch for an Instrument backfills from the date of its earliest current trade
Transaction; with none, the fetch falls back to a bounded look-back. The query lives in core
(``reference`` depends on core only) and reads current rows only.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from finance360.core import transaction


def earliest_trade_dates(session: Session) -> dict[int, date]:
    """Instrument id -> date of its earliest current trade Transaction."""
    return transaction.earliest_trade_dates(session)
