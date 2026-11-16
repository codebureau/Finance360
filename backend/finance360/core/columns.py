"""Column helpers for exact numbers (ADR-0009 section 1). Nothing here rounds.

* ``money_columns`` / ``money_composite``: a Money attribute stored as ``<name>_minor`` /
  ``<name>_ccy``. Usage inside a declarative class::

      price_minor, price_ccy = money_columns("price")
      price: Mapped[Money] = money_composite(price_minor, price_ccy)

* ``ScaledDecimal(scale)``: a ``Decimal`` stored as an INTEGER count of ``10**-scale`` units, for
  rates and quantities. It never rounds: over-precise values are rejected.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, Dialect, Integer, String
from sqlalchemy.orm import Composite, MappedColumn, composite, mapped_column
from sqlalchemy.types import TypeDecorator

from finance360.core.money import INT64_MAX, INT64_MIN, Money, MoneyError, decimal_parts, opaque


def money_columns(name: str) -> tuple[MappedColumn[int], MappedColumn[str]]:
    """The ``<name>_minor`` / ``<name>_ccy`` column pair (both NOT NULL, ccy CHECK length 3).

    Assign to attributes named exactly ``<name>_minor`` and ``<name>_ccy``.
    """
    minor = mapped_column(BigInteger, nullable=False)
    ccy = mapped_column(
        String(3),
        CheckConstraint(f"length({name}_ccy) = 3", name=f"{name}_ccy_len"),
        nullable=False,
    )
    return minor, ccy


def money_composite(minor: MappedColumn[int], ccy: MappedColumn[str]) -> Composite[Money]:
    """Map the column pair to one ``Money`` attribute."""
    return composite(Money, minor, ccy)


class ScaledDecimal(TypeDecorator[Decimal]):
    """``Decimal`` with a fixed number of decimal places, stored as an INTEGER of scaled units."""

    impl = Integer
    cache_ok = True

    def __init__(self, scale: int) -> None:
        untyped_scale = opaque(scale)
        if (
            isinstance(untyped_scale, bool)
            or not isinstance(untyped_scale, int)
            or untyped_scale < 0
        ):
            raise ValueError("ScaledDecimal scale must be a non-negative integer")
        super().__init__()
        self.scale = scale

    def process_bind_param(self, value: Any, dialect: Dialect) -> int | None:
        if value is None:
            return None
        if isinstance(value, float):
            raise TypeError("ScaledDecimal rejects floats; pass a Decimal or int")
        if isinstance(value, bool) or not isinstance(value, Decimal | int):
            raise TypeError(f"ScaledDecimal needs a Decimal or int, got {type(value).__name__}")
        d = Decimal(value)
        if not d.is_finite():
            raise MoneyError(f"ScaledDecimal needs a finite value: {value}")
        negative, units, exp = decimal_parts(d)
        shift = exp + self.scale
        if shift >= 0:
            units *= 10**shift
        else:
            units, remainder = divmod(units, 10**-shift)
            if remainder:
                raise MoneyError(f"{value} has more than {self.scale} decimal places")
        units = -units if negative else units
        if not INT64_MIN <= units <= INT64_MAX:
            raise MoneyError(f"{value} does not fit a ScaledDecimal({self.scale})")
        return units

    def process_result_value(self, value: Any, dialect: Dialect) -> Decimal | None:
        if value is None:
            return None
        n = int(value)
        return Decimal((1 if n < 0 else 0, tuple(int(c) for c in str(abs(n))), -self.scale))
