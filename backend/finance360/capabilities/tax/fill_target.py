"""The ``tax.income_statement`` Fill target (ADR-0026 section 4): what the income statement form
can be filled with from a Reading.

Plain data, built here beside the form it describes. Tax may not import Document intake
(ADR-0024 section 7), so ``main.py``, the composition root, turns this into a ``FillTarget`` and
registers it before the Document profiles that name it. A test pins these keys to the form's.
"""

from __future__ import annotations

from dataclasses import fields

from finance360.capabilities.tax.link_kind import KEY as LINK_KIND
from finance360.capabilities.tax.record_types.income_statement import (
    TEXT_FIELDS,
    IncomeStatement,
)

KEY = "tax.income_statement"
TITLE = "Tax income statement"
#: A statement carries the financial year it covers, which a Reading is checked against.
HAS_PERIOD = True

#: ``(key, type)``: every field of the statement (money or text), plus the ``period`` a Reading
#: can give (the year it ends in), which is not a stored figure.
FIELDS: tuple[tuple[str, str], ...] = (
    ("period", "fy"),
    *((f.name, "text" if f.name in TEXT_FIELDS else "money") for f in fields(IncomeStatement)),
)

__all__ = ["FIELDS", "HAS_PERIOD", "KEY", "LINK_KIND", "TITLE"]
