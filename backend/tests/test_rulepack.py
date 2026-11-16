"""The Rule pack contract, ``materialise``, the content hash and the registry (ADR-0018).

Pure unit tests over invented test packs. No database. Money and rates are exact integers.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

import pytest
from hypothesis import given
from hypothesis import strategies as st

from finance360.core import rulepack as rp
from finance360.core.money import Money
from finance360.core.rate import Rate
from finance360.core.rulepack import Age, AgeRule, Param, RulePack, RulePackError, Span


def aud(minor: int) -> Money:
    return Money(minor, "AUD")


def pack(
    params: tuple[Param, ...],
    years: Mapping[int, Mapping[str, rp.Value]],
    revision: int = 1,
) -> RulePack:
    return RulePack("test", "xx", revision, params, years)


CPI_PACK = pack(
    (
        Param("floor", rp.MONEY, rp.CPI, "invented", "year"),
        Param("share", rp.RATE, rp.CPI, "invented"),
    ),
    {2027: {"floor": aud(1_000_000), "share": Rate.from_percent("12")}},
)

# A frozen threshold, with a stepped rate that changes in 2029.
MIXED_PACK = pack(
    (
        Param("threshold", rp.MONEY, rp.FROZEN, "invented", "year"),
        Param("share", rp.RATE, rp.CPI, "invented"),
    ),
    {
        2027: {"threshold": aud(1_000_000), "share": Rate.from_percent("10")},
        2029: {"share": Rate.from_percent("11")},
    },
)


# --- ages ----------------------------------------------------------------------------------


def test_an_age_is_exact_years_and_months_and_orders_by_total_months() -> None:
    assert Age(66, 6) > Age(66) and Age(66, 6) < Age(67)
    assert Age(65, 6).total_months == 786
    assert str(Age(67)) == "67" and str(Age(66, 6)) == "66y6m"
    assert Age.parse("66y6m") == Age(66, 6) and Age.parse(" 67 ") == Age(67)


@pytest.mark.parametrize("text", ["66.5", "66 and a half", "y6m", "66y12m", "-1", "", "6 6"])
def test_a_decimal_or_malformed_age_is_rejected(text: str) -> None:
    with pytest.raises(RulePackError):
        Age.parse(text)


def test_an_age_refuses_a_float_and_an_out_of_range_month() -> None:
    with pytest.raises(TypeError):
        Age(66.5)  # type: ignore[arg-type]
    with pytest.raises(RulePackError):
        Age(66, 12)


@given(st.integers(min_value=0, max_value=1500))
def test_age_months_round_trip_through_text_and_total(total: int) -> None:
    age = Age.from_months(total)
    assert age.total_months == total
    assert Age.parse(str(age)) == age


# --- financial years -----------------------------------------------------------------------


def test_the_fy_runs_1_july_to_30_june_and_is_keyed_by_the_year_it_ends_in() -> None:
    assert rp.fy_of(date(2026, 6, 30)) == 2026
    assert rp.fy_of(date(2026, 7, 1)) == 2027
    assert rp.fy_of(date(2027, 1, 1)) == 2027
    assert rp.fy_label(2027) == "2026-27"
    assert rp.fy_label(2100) == "2099-00"


# --- materialise ---------------------------------------------------------------------------


def test_a_cpi_only_pack_yields_one_span_whatever_the_horizon() -> None:
    spans = rp.materialise(CPI_PACK, 2027, 40, Rate.from_percent("3"))
    assert spans == (Span(0, {"floor": aud(1_000_000), "share": Rate.from_percent("12")}),)


def test_a_cpi_value_is_constant_in_real_terms_beyond_the_last_known_year() -> None:
    (span,) = rp.materialise(CPI_PACK, 2030, 5, Rate.from_percent("9"))
    assert span.values["floor"] == aud(1_000_000)


def test_a_frozen_value_falls_by_inflation_one_scale_per_year_from_the_last_known() -> None:
    inflation = Rate.from_percent("10")  # 10% so the arithmetic is checkable by hand
    spans = rp.materialise(MIXED_PACK, 2027, 4, inflation)
    # FY 2027 is the last known threshold: unchanged. Then 1,000,000 / 1.1^n, rounded once each.
    by_t = {}
    for i, span in enumerate(spans):
        end = spans[i + 1].first_t if i + 1 < len(spans) else 4
        for t in range(span.first_t, end):
            by_t[t] = span.values
    assert [by_t[t]["threshold"].amount_minor for t in range(4)] == [
        1_000_000,
        909_091,  # 1,000,000 / 1.1
        826_446,  # 1,000,000 / 1.21
        751_315,  # 1,000,000 / 1.331
    ]
    # The share steps up in FY 2029 (t = 2) and then carries (cpi): spans split exactly there.
    assert [s.values["share"] for s in spans if s.first_t in (0, 2)] == [
        Rate.from_percent("10"),
        Rate.from_percent("11"),
    ]


def test_frozen_rounding_is_not_compounded() -> None:
    """Each year is one scale from the last known value, not a chain of rounded steps.

    At 3.3% from 10,000.07, year 5 chained (rounding every year) would be 8,501.62; one scale from
    the start is 8,501.61. The pinned figure is the exact one.
    """
    frozen = pack((Param("t", rp.MONEY, rp.FROZEN, "invented"),), {2027: {"t": aud(1_000_007)}})
    spans = rp.materialise(frozen, 2027, 6, Rate.from_percent("3.3"))
    values = [s.values["t"] for s in spans]
    assert len(spans) == 6  # it falls every year, so a span each
    assert values[5] == aud(850_161) and values[5] != aud(850_162)


def test_overrides_replace_a_parameter_for_every_year() -> None:
    spans = rp.materialise(
        MIXED_PACK, 2027, 5, Rate.from_percent("2"), {"threshold": aud(5), "share": Rate.zero()}
    )
    assert spans == (Span(0, {"threshold": aud(5), "share": Rate.zero()}),)


def test_an_override_of_the_wrong_kind_or_an_unknown_key_is_refused() -> None:
    with pytest.raises(RulePackError):
        rp.materialise(CPI_PACK, 2027, 2, Rate.zero(), {"floor": Rate.zero()})
    with pytest.raises(RulePackError):
        rp.materialise(CPI_PACK, 2027, 2, Rate.zero(), {"nope": aud(1)})


def test_a_year_before_the_first_known_value_is_refused() -> None:
    with pytest.raises(RulePackError, match="floor"):
        rp.materialise(CPI_PACK, 2026, 2, Rate.zero())


@given(
    st.integers(min_value=0, max_value=10**12),
    st.integers(min_value=0, max_value=200_000),
    st.integers(min_value=1, max_value=60),
)
def test_a_frozen_value_never_rises_and_never_goes_negative(
    minor: int, ppm: int, years: int
) -> None:
    frozen = pack((Param("t", rp.MONEY, rp.FROZEN, "invented"),), {2027: {"t": aud(minor)}})
    spans = rp.materialise(frozen, 2027, years, Rate(ppm))
    seen = [s.values["t"].amount_minor for s in spans]  # type: ignore[union-attr]
    assert seen == sorted(seen, reverse=True) and min(seen) >= 0


# --- validation ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "build",
    [
        lambda: pack((), {}),  # no year
        lambda: pack((Param("a", rp.MONEY, rp.CPI, "s"),) * 2, {2027: {}}),  # duplicate key
        lambda: pack((Param("a", "float", rp.CPI, "s"),), {2027: {}}),  # unknown kind
        lambda: pack((Param("a", rp.MONEY, "wages", "s"),), {2027: {}}),  # policy not built
        lambda: pack((Param("a", rp.RATE, rp.FROZEN, "s"),), {2027: {}}),  # only money is frozen
        lambda: pack((Param("a", rp.MONEY, rp.CPI, "s"),), {2027: {"b": aud(1)}}),  # undeclared
        lambda: pack((Param("a", rp.MONEY, rp.CPI, "s"),), {2027: {"a": 1}}),  # wrong kind
        lambda: pack((Param("a", rp.INT, rp.CPI, "s"),), {2027: {"a": True}}),  # a bool is no int
        lambda: pack((Param("a", rp.INT, rp.CPI, "s"),), {2027: {"a": 1.5}}),  # type: ignore[dict-item]
        lambda: pack((), {2027: {}}, revision=0),
    ],
)
def test_a_malformed_pack_is_refused(build: object) -> None:
    with pytest.raises(RulePackError):
        build()  # type: ignore[operator]


def test_age_keys_must_not_collide_with_parameters() -> None:
    clash = AgeRule("a", "A", "s", lambda _d: Age(60))
    with pytest.raises(RulePackError):
        RulePack("t", "xx", 1, (Param("a", rp.INT, rp.CPI, "s"),), {2027: {}}, (clash,))


# --- hash and label ------------------------------------------------------------------------


def test_the_content_hash_is_stable_and_changes_with_any_data() -> None:
    assert CPI_PACK.content_hash() == CPI_PACK.content_hash()
    assert len(CPI_PACK.content_hash()) == 64
    other = pack(
        CPI_PACK.params,
        {2027: {"floor": aud(1_000_001), "share": Rate.from_percent("12")}},
    )
    assert other.content_hash() != CPI_PACK.content_hash()
    moved = pack(CPI_PACK.params, {2028: dict(CPI_PACK.years[2027])})
    assert moved.content_hash() != CPI_PACK.content_hash()


def test_the_version_label_names_the_pack_the_last_known_fy_and_the_revision() -> None:
    assert CPI_PACK.version_label == "test/2026-27/r1"
    assert MIXED_PACK.last_fy == 2029
    assert pack(MIXED_PACK.params, MIXED_PACK.years, revision=3).version_label == "test/2028-29/r3"


# --- registry ------------------------------------------------------------------------------


def test_registry_looks_a_pack_up_by_jurisdiction_and_refuses_a_second_for_a_key() -> None:
    rp.register(CPI_PACK)
    try:
        rp.register(CPI_PACK)  # the same pack twice is harmless
        assert rp.get("xx") is CPI_PACK
        with pytest.raises(ValueError, match="already registered"):
            rp.register(pack((), {2027: {}}))
    finally:
        rp.unregister("xx")
    assert rp.get("xx") is None


# --- brackets and provisional markers (ADR-0019 section 4) ----------------------


def bands(*pairs: tuple[int, str]) -> tuple[rp.Bracket, ...]:
    return tuple(rp.Bracket(aud(over), Rate.from_percent(rate)) for over, rate in pairs)


TABLE = Param("tax.table", rp.BRACKETS, rp.CPI, "invented", "year")
THRESHOLD = Param("tax.threshold", rp.MONEY, rp.FROZEN, "invented", "year")


def table_pack(
    table: tuple[rp.Bracket, ...],
    provisional: Mapping[int, Mapping[str, str]] | None = None,
) -> RulePack:
    return RulePack(
        "test",
        "xx",
        1,
        (TABLE, THRESHOLD),
        {2027: {"tax.table": table, "tax.threshold": aud(5_000)}},
        provisional=provisional or {},
    )


BASE_TABLE = bands((0, "0"), (1_000_000, "10"), (4_000_000, "25.5"))


def test_a_brackets_entry_round_trips_through_canonical_json() -> None:
    entry = rp.value_json(BASE_TABLE)
    assert entry == [
        {"rate_ppm": 0, "threshold": {"ccy": "AUD", "minor": 0}},
        {"rate_ppm": 100_000, "threshold": {"ccy": "AUD", "minor": 1_000_000}},
        {"rate_ppm": 255_000, "threshold": {"ccy": "AUD", "minor": 4_000_000}},
    ]
    assert rp.value_from_json(rp.BRACKETS, entry) == BASE_TABLE


@given(
    st.lists(st.integers(1, 10**9), min_size=0, max_size=6, unique=True).map(sorted),
    st.lists(st.integers(-999_999, 1_000_000), min_size=7, max_size=7),
)
def test_any_ascending_table_round_trips_exactly(overs: list[int], ppms: list[int]) -> None:
    table = tuple(
        rp.Bracket(aud(over), Rate(ppm)) for over, ppm in zip([0, *overs], ppms, strict=False)
    )
    assert rp.value_from_json(rp.BRACKETS, rp.value_json(table)) == table


def test_editing_a_band_changes_the_revision_hash() -> None:
    base = table_pack(BASE_TABLE).content_hash()
    assert table_pack(BASE_TABLE).content_hash() == base
    for edited in (
        bands((0, "0"), (1_000_000, "10"), (4_000_001, "25.5")),  # a threshold
        bands((0, "0"), (1_000_000, "10"), (4_000_000, "25.4")),  # a rate
        BASE_TABLE[:2],  # a band removed
    ):
        assert table_pack(edited).content_hash() != base


def test_a_table_must_be_non_empty_and_ascend_strictly() -> None:
    for bad in ((), bands((0, "0"), (5, "10"), (5, "20")), bands((10, "0"), (5, "10"))):
        with pytest.raises(RulePackError):
            table_pack(bad)


def test_a_provisional_marker_changes_the_hash_and_a_lookup_sees_it() -> None:
    firm = table_pack(BASE_TABLE)
    marked = table_pack(BASE_TABLE, {2027: {"tax.threshold": "announced, not yet law"}})
    assert marked.content_hash() != firm.content_hash()
    assert marked.provisional_note(2027, "tax.threshold") == "announced, not yet law"
    assert marked.provisional_note(2027, "tax.table") is None
    assert marked.provisional_keys(2027) == {"tax.threshold": "announced, not yet law"}
    assert firm.provisional_keys(2027) == {} and firm.provisional_note(2027, "tax.table") is None
    reworded = table_pack(BASE_TABLE, {2027: {"tax.threshold": "a different note"}})
    assert reworded.content_hash() not in (marked.content_hash(), firm.content_hash())


def test_a_provisional_marker_needs_a_value_and_a_note() -> None:
    with pytest.raises(RulePackError, match="no value"):
        table_pack(BASE_TABLE, {2027: {"tax.nothing": "note"}})
    with pytest.raises(RulePackError, match="note"):
        table_pack(BASE_TABLE, {2027: {"tax.threshold": "  "}})


def test_tax_keys_are_not_projected_or_overridable() -> None:
    tax = table_pack(BASE_TABLE)
    assert tax.keys == ()
    assert rp.materialise(tax, 2027, 3, Rate.zero()) == (Span(0, {}),)
