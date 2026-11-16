"""Money and ScaledDecimal through a real temporary SQLite database (ADR-0009 section 1).

The table here is test-only and lives on its own metadata, so it never reaches the app's migrations.
No floats anywhere, including in strategies.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import Column, Engine, Float, Integer, MetaData, Numeric, String, Table, text
from sqlalchemy.dialects import sqlite
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

import finance360.core.models  # noqa: F401  (registers every app table on Base.metadata)
from finance360.core.columns import ScaledDecimal, money_columns, money_composite
from finance360.core.db import Base, db_path, make_engine
from finance360.core.money import CURRENCY_EXPONENTS, INT64_MAX, INT64_MIN, Money, MoneyError

DIALECT = sqlite.dialect()


class _TestBase(DeclarativeBase):
    metadata = MetaData()


class Thing(_TestBase):
    __tablename__ = "thing"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(20), default="")
    price_minor, price_ccy = money_columns("price")
    price: Mapped[Money] = money_composite(price_minor, price_ccy)
    rate: Mapped[Decimal] = mapped_column(ScaledDecimal(10), default=Decimal(1))
    qty: Mapped[Decimal | None] = mapped_column(ScaledDecimal(4), default=None)


@pytest.fixture(scope="module")
def engine(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Engine]:
    eng = make_engine(db_path(tmp_path_factory.mktemp("db")))
    _TestBase.metadata.create_all(eng)
    yield eng
    eng.dispose()


def _find_float_columns(metadata: MetaData) -> list[str]:
    """Names of Numeric/Float columns (Numeric is a float in SQLite); Float subclasses Numeric."""
    return [
        f"{table.name}.{col.name}"
        for table in metadata.tables.values()
        for col in table.columns
        if isinstance(col.type, Numeric | Float)
    ]


def _round_trip(engine: Engine, thing: Thing) -> Thing:
    with Session(engine) as s:
        s.add(thing)
        s.commit()
        key = thing.id
    with Session(engine) as s:  # a fresh session: nothing is served from the identity map
        loaded = s.get(Thing, key)
        assert loaded is not None
        s.expunge(loaded)
        return loaded


# ---- the database -------------------------------------------------------------------------------


def test_engine_uses_wal_and_enforces_foreign_keys(engine: Engine) -> None:
    with engine.connect() as c:
        assert c.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert c.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_money_is_stored_as_two_integer_text_columns(engine: Engine) -> None:
    with Session(engine) as s:
        s.add(Thing(price=Money(-12_345, "JPY")))
        s.commit()
    with engine.connect() as c:
        row = c.execute(
            text("SELECT typeof(price_minor), typeof(price_ccy) FROM thing ORDER BY id DESC")
        ).first()
    assert row == ("integer", "text")


def test_currency_column_rejects_wrong_length_codes(engine: Engine) -> None:
    with engine.begin() as c, pytest.raises(IntegrityError):
        c.execute(
            text("INSERT INTO thing (label, price_minor, price_ccy, rate) VALUES ('', 1, 'AU', 0)")
        )


# ---- Money round-trip ---------------------------------------------------------------------------


@settings(max_examples=150, deadline=None)
@given(
    amount=st.integers(INT64_MIN, INT64_MAX),
    ccy=st.sampled_from(sorted(CURRENCY_EXPONENTS)),
)
def test_money_round_trips_exactly_for_any_valid_value(
    engine: Engine, amount: int, ccy: str
) -> None:
    loaded = _round_trip(engine, Thing(price=Money(amount, ccy)))
    assert loaded.price == Money(amount, ccy)
    assert type(loaded.price.amount_minor) is int


def test_money_attribute_can_be_replaced_and_persisted(engine: Engine) -> None:
    with Session(engine) as s:
        t = Thing(price=Money(100, "AUD"))
        s.add(t)
        s.commit()
        t.price = Money(250, "AUD")
        s.commit()
        key = t.id
    with Session(engine) as s:
        loaded = s.get(Thing, key)
        assert loaded is not None
        assert loaded.price == Money(250, "AUD")


def test_money_columns_can_be_queried_by_composite(engine: Engine) -> None:
    with Session(engine) as s:
        s.add(Thing(label="needle", price=Money(7_777_777, "NZD")))
        s.commit()
        found = s.query(Thing).filter(Thing.price == Money(7_777_777, "NZD")).all()
        assert [t.label for t in found] == ["needle"]


# ---- ScaledDecimal ------------------------------------------------------------------------------


@settings(max_examples=150, deadline=None)
@given(
    units=st.integers(INT64_MIN, INT64_MAX),
    qty_units=st.one_of(st.none(), st.integers(-(10**12), 10**12)),
)
def test_scaled_decimal_round_trips_exactly(
    engine: Engine, units: int, qty_units: int | None
) -> None:
    rate = Decimal(units).scaleb(-10)
    qty = None if qty_units is None else Decimal(qty_units).scaleb(-4)
    loaded = _round_trip(engine, Thing(price=Money(0, "AUD"), rate=rate, qty=qty))
    assert loaded.rate == rate
    assert loaded.qty == qty
    with engine.connect() as c:  # stored as the raw scaled integer
        raw = c.execute(text("SELECT rate FROM thing WHERE id = :i"), {"i": loaded.id}).scalar()
    assert raw == units


def test_scaled_decimal_accepts_fewer_places_and_integers_and_trailing_zeros(
    engine: Engine,
) -> None:
    loaded = _round_trip(engine, Thing(price=Money(0, "AUD"), rate=Decimal("1.5")))
    assert loaded.rate == Decimal("1.5000000000")
    assert _round_trip(engine, Thing(price=Money(0, "AUD"), rate=3)).rate == Decimal(3)  # type: ignore[arg-type]
    assert _round_trip(
        engine, Thing(price=Money(0, "AUD"), rate=Decimal("2.50000000000"))
    ).rate == Decimal("2.5")
    assert _round_trip(engine, Thing(price=Money(0, "AUD"), rate=Decimal("1E-10"))).rate == Decimal(
        "1E-10"
    )


@pytest.mark.parametrize("bad", [1.5, 0.0, float("nan")])
def test_scaled_decimal_rejects_floats(engine: Engine, bad: float) -> None:
    with Session(engine) as s:
        s.add(Thing(price=Money(0, "AUD"), rate=bad))  # type: ignore[arg-type]
        with pytest.raises(Exception, match="float") as err:
            s.commit()
        assert isinstance(err.value.__cause__ or err.value.orig, TypeError)  # type: ignore[attr-defined]
        s.rollback()


@pytest.mark.parametrize("bad", ["1.5", True, object()])
def test_scaled_decimal_rejects_other_types(bad: object) -> None:
    with pytest.raises(TypeError):
        ScaledDecimal(2).process_bind_param(bad, DIALECT)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "bad", [Decimal("0.00000000001"), Decimal("1.12345678901"), Decimal("-0.123456789012")]
)
def test_scaled_decimal_rejects_over_precise_values_instead_of_rounding(bad: Decimal) -> None:
    with pytest.raises(MoneyError, match="decimal places"):
        ScaledDecimal(10).process_bind_param(bad, DIALECT)


@pytest.mark.parametrize("bad", [Decimal("NaN"), Decimal("Infinity"), Decimal("1E+20")])
def test_scaled_decimal_rejects_non_finite_and_out_of_range(bad: Decimal) -> None:
    with pytest.raises(MoneyError):
        ScaledDecimal(10).process_bind_param(bad, DIALECT)


@given(places=st.integers(0, 12), n=st.integers(-(10**9), 10**9))
def test_scaled_decimal_bind_then_result_is_identity(places: int, n: int) -> None:
    t = ScaledDecimal(places)
    value = Decimal(n).scaleb(-places)
    assert t.process_result_value(t.process_bind_param(value, DIALECT), DIALECT) == value


def test_scaled_decimal_none_passes_through() -> None:
    t = ScaledDecimal(4)
    assert t.process_bind_param(None, DIALECT) is None
    assert t.process_result_value(None, DIALECT) is None


@pytest.mark.parametrize("scale", [-1, 1.5, True])
def test_scaled_decimal_scale_must_be_a_non_negative_int(scale: object) -> None:
    with pytest.raises(ValueError):
        ScaledDecimal(scale)  # type: ignore[arg-type]


# ---- no float path ------------------------------------------------------------------------------


def test_no_numeric_or_float_column_exists_in_the_app_schema() -> None:
    assert _find_float_columns(Base.metadata) == []


def test_the_float_column_guard_actually_detects_numeric_and_float() -> None:
    md = MetaData()
    Table(
        "worse",
        md,
        Column("id", Integer, primary_key=True),
        Column("n", Numeric(10, 2)),
        Column("f", Float),
    )
    assert _find_float_columns(md) == ["worse.n", "worse.f"]
    assert _find_float_columns(_TestBase.metadata) == []
