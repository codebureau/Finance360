"""The Fill target registry (ADR-0026 section 4): a profile is validated against the target it
names when it registers, so a new document variant is a JSON profile and nothing else."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import fields as dataclass_fields

import pytest

from finance360.capabilities.documents import registry
from finance360.capabilities.documents.profile import Profile, ProfileError, load_profile
from finance360.capabilities.tax import fill_target
from finance360.capabilities.tax.record_types.income_statement import IncomeStatement
from finance360.plugins.documents.ato_income_statement import PROFILE
from support import register_income_statement_target

TARGET = "test.invented_form"


def invented_target() -> registry.FillTarget:
    return registry.FillTarget(
        key=TARGET,
        title="An invented form",
        fields=(registry.TargetField("period", "fy"), registry.TargetField("total", "money")),
        has_period=True,
        link_kind="tax_record",
    )


def invented_profile(**changes: object) -> Profile:
    spec: dict[str, object] = {
        "key": "invented-profile",
        "version": 1,
        "title": "Invented",
        "target": TARGET,
        "claim": ["Invented statement"],
        "fields": [{"key": "total", "type": "money", "labels": ["Total"], "pair": "same_row"}],
    }
    return load_profile({**spec, **changes})


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    register_income_statement_target()
    registry.register_target(invented_target())
    yield
    registry.unregister(PROFILE.key)
    registry.unregister("invented-profile")
    registry.unregister_target(TARGET)


def test_the_shipped_ato_profile_validates_against_its_target() -> None:
    registry.register(PROFILE)
    assert registry.get_target("tax.income_statement") is not None
    assert PROFILE in registry.for_target("tax.income_statement")


def test_a_good_profile_for_a_registered_target_registers() -> None:
    profile = invented_profile()
    registry.register(profile)
    assert registry.for_target(TARGET) == [profile]


def test_a_profile_for_an_unknown_target_is_refused_with_a_clear_message() -> None:
    with pytest.raises(ProfileError, match=r"unknown target 'no.such_form'"):
        registry.register(invented_profile(target="no.such_form"))
    assert registry.for_target("no.such_form") == []


def test_a_profile_field_the_target_does_not_declare_is_refused() -> None:
    bad = invented_profile(
        fields=[{"key": "surprise", "type": "money", "labels": ["Surprise"], "pair": "same_row"}]
    )
    with pytest.raises(ProfileError, match=r"field 'surprise', which target '.*' does not have"):
        registry.register(bad)
    assert registry.for_target(TARGET) == []


def test_a_profile_field_of_the_wrong_type_is_refused() -> None:
    bad = invented_profile(
        fields=[{"key": "total", "type": "text", "labels": ["Total"], "pair": "same_row"}]
    )
    with pytest.raises(ProfileError, match="reads field 'total' as text"):
        registry.register(bad)


def test_a_different_target_under_the_same_key_is_a_programming_error() -> None:
    clash = registry.FillTarget(TARGET, "Other", (), False, "tax_record")
    with pytest.raises(ValueError, match="already registered"):
        registry.register_target(clash)
    registry.register_target(invented_target())  # the same target again is harmless


def test_the_income_statement_target_matches_the_form() -> None:
    """Pinned to the form's own fields, so a new statement figure cannot be forgotten here."""
    target = registry.get_target(fill_target.KEY)
    assert target is not None
    assert target.has_period is True and target.link_kind == "tax_record"
    declared = {f.key for f in target.fields}
    assert declared == {"period", *(f.name for f in dataclass_fields(IncomeStatement))}
    assert {f.key for f in PROFILE.fields} <= declared
