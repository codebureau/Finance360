"""The rba FX provider plug-in: parsing the F11.1 layout, the single inversion (half-even to 10
d.p.), and the provider over a stubbed provider. The table below is invented (recorded in the RBA's
layout); nothing here reaches the network."""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from finance360.capabilities.reference.providers import ProviderError
from finance360.core.fx import RATE_SCALE, parse_rate
from finance360.plugins.providers.rba import RbaProvider, make_provider
from finance360.plugins.providers.rba.parse import LAYOUT_ERROR, invert, parse_f11
from finance360.plugins.providers.rba.sample import sample_f11
from finance360.plugins.providers.rba.transport import fetch_f11

#: Invented figures in the RBA's F11.1 layout: header rows, a blank cell (a holiday), an Index
#: column and an SDR column that are not currencies we hold, and a currency we never ask for.
F11 = (
    b"F11.1  EXCHANGE RATES\r\n"
    b"Title,A$1=USD,Trade-weighted Index May 1970 = 100,A$1=GBP,A$1=JPY,A$1=SDR\r\n"
    b"Description,AUD/USD,Index,AUD/GBP,AUD/JPY,AUD/SDR\r\n"
    b"Frequency,Daily,Daily,Daily,Daily,Daily\r\n"
    b"Units,USD,Index,GBP,JPY,SDR\r\n"
    b"\r\n"
    b"Source,Sample,Sample,Sample,Sample,Sample\r\n"
    b"Series ID,S1,S2,S3,S4,S5\r\n"
    b"01-Oct-2026,0.6500,61.40,0.4902,97.10,0.4700\r\n"
    b"02-Oct-2026,0.6510,61.50,0.4900,97.25,0.4710\r\n"
    b"05-Oct-2026,0.6520,61.60,,97.50,0.4720\r\n"  # GBP blank: no figure that day
    b"06-Oct-2026,0.6530,61.70,0.4890,,0.4730\r\n"
)


def test_parse_reads_currency_columns_by_the_units_row_and_skips_the_rest() -> None:
    table = parse_f11(F11)
    assert set(table.published) == {"USD", "GBP", "JPY"}  # not Index, not SDR
    assert table.published["GBP"] == {
        date(2026, 10, 1): Decimal("0.4902"),
        date(2026, 10, 2): Decimal("0.4900"),
        date(2026, 10, 6): Decimal("0.4890"),  # the blank on the 5th is a gap, not a zero
    }
    assert table.published["JPY"][date(2026, 10, 1)] == Decimal("97.10")


def test_a_file_that_is_not_the_table_is_a_provider_error() -> None:
    for junk in (b"", b"<html>maintenance</html>", b"Units,\r\n", b"Title,A\r\n01-Oct-2026,1\r\n"):
        with pytest.raises(ProviderError) as caught:
            parse_f11(junk)
        assert str(caught.value) == LAYOUT_ERROR


def test_the_provider_inverts_once_and_keeps_the_published_figure_in_the_note() -> None:
    provider = RbaProvider(lambda: F11)
    fetched = provider.fetch_rates(["GBP", "JPY"], date(2026, 10, 1), date(2026, 10, 6))
    gbp = {q.as_at: q for q in fetched.quotes if q.from_ccy == "GBP"}
    assert gbp[date(2026, 10, 1)].rate == Decimal("2.0399836801")  # 1 / 0.4902, 10 d.p.
    assert "A$1 = 0.4902 GBP" in gbp[date(2026, 10, 1)].note
    assert fetched.unsupported == ()
    assert {q.from_ccy for q in fetched.quotes} == {"GBP", "JPY"}  # USD was not asked for


def test_only_the_asked_range_is_returned_and_unpublished_currencies_are_reported() -> None:
    provider = RbaProvider(lambda: F11)
    fetched = provider.fetch_rates(["GBP", "CAD"], date(2026, 10, 2), date(2026, 10, 2))
    assert [(q.from_ccy, q.as_at) for q in fetched.quotes] == [("GBP", date(2026, 10, 2))]
    assert fetched.unsupported == ("CAD",)


def test_a_failing_provider_surfaces_as_a_provider_error() -> None:
    def down() -> bytes:
        raise ProviderError("The RBA could not be reached (URLError).")

    with pytest.raises(ProviderError):
        RbaProvider(down).fetch_rates(["GBP"], date(2026, 10, 1), date(2026, 10, 2))


# --- the inversion --------------------------------------------------------------------------


def test_invert_is_half_even_at_ten_places() -> None:
    assert invert(Decimal("0.4902")) == Decimal("2.0399836801")
    assert invert(Decimal("2")) == Decimal("0.5000000000")
    assert invert(Decimal("3")) == Decimal("0.3333333333")
    assert invert(Decimal("0.7")) == Decimal("1.4285714286")  # ...2857142857 rounds up
    # An exact tie at the 11th place goes to the even neighbour (a tie needs a published figure
    # whose reciprocal terminates at 11 places: 1/2^11 = 0.00048828125).
    assert invert(Decimal(2048)) == Decimal("0.0004882812")  # ...8125 -> even 2
    assert invert(Decimal(2048 * 3)) == (Decimal(1) / Decimal(6144)).quantize(
        Decimal(1).scaleb(-RATE_SCALE), rounding=ROUND_HALF_EVEN
    )


@pytest.mark.parametrize("bad", [Decimal(0), Decimal(-1), Decimal("1e12")])
def test_a_figure_that_cannot_give_a_positive_rate_is_dropped(bad: Decimal) -> None:
    assert invert(bad) is None


@given(st.decimals(min_value=Decimal("0.0001"), max_value=Decimal("20000"), places=4))
def test_the_inverse_is_a_valid_rate_within_half_a_unit_of_the_true_reciprocal(
    published: Decimal,
) -> None:
    rate = invert(published)
    assert rate is not None and rate > 0
    parse_rate(format(rate, "f"))  # stored-rate validator accepts it: positive, at most 10 places
    assert abs(rate * published - 1) <= published * Decimal("0.5e-10")


# --- transport and sample -------------------------------------------------------------------


def test_the_live_transport_is_only_ever_https() -> None:
    from finance360.plugins.providers.rba import transport

    assert transport.URL.startswith("https://www.rba.gov.au/")
    assert callable(fetch_f11)


def test_the_sample_table_parses_with_the_real_parser_and_has_weekdays_up_to_today() -> None:
    table = parse_f11(sample_f11(date(2026, 10, 20)))
    assert {"USD", "GBP", "EUR", "JPY"} <= set(table.published)
    days = table.published["GBP"]
    assert max(days) == date(2026, 10, 20) and all(d.weekday() < 5 for d in days)
    provider = make_provider(sample=True)
    assert "offline sample" in provider.name
