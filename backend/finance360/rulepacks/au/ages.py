"""Australian ages fixed by law, as pure functions of date of birth (ADR-0018 section 1).

Each schedule is a table of ``(first date of birth, age)``: a person born on or after a date, and
before the next row's date, gets that row's age. Transition dates are golden-tested in
``tests/test_rulepack_au.py`` (the day before and the day of each).

Sources are public law and public guidance only. The legislation citations name the instrument;
the guidance pages are what the schedules were checked against (see each constant).
"""

from __future__ import annotations

from datetime import date

from finance360.core.rulepack import Age

Schedule = tuple[tuple[date, Age], ...]

#: Preservation age: the age from which preserved super may be accessed on retiring (a condition of
#: release). Born before 1 July 1960: 55; each later July-to-June birth year adds a year; born
#: from 1 July 1964: 60.
#: Source: Superannuation Industry (Supervision) Regulations 1994, reg 6.01 (definition of
#: preservation age); ATO, "Key superannuation rates and thresholds - Payments from super"
#: https://www.ato.gov.au/tax-rates-and-codes/key-superannuation-rates-and-thresholds/payments-from-super
PRESERVATION_AGE_SOURCE = (
    "SIS Regulations 1994 reg 6.01; ATO, Key superannuation rates and thresholds: "
    "Payments from super"
)
PRESERVATION_AGE: Schedule = (
    (date.min, Age(55)),
    (date(1960, 7, 1), Age(56)),
    (date(1961, 7, 1), Age(57)),
    (date(1962, 7, 1), Age(58)),
    (date(1963, 7, 1), Age(59)),
    (date(1964, 7, 1), Age(60)),
)

#: Unrestricted super age: preserved super is fully accessible on reaching 65, retired or not.
#: Source: SIS Regulations 1994 Schedule 1 (condition of release: attaining age 65); ATO, as above.
UNRESTRICTED_SUPER_AGE_SOURCE = (
    "SIS Regulations 1994 Sch 1 (attaining age 65); ATO, Key superannuation rates and "
    "thresholds: Payments from super"
)
UNRESTRICTED_SUPER_AGE = Age(65)

#: Age Pension age. 65 for those born before 1 July 1952 (a simplification: women born before
#: about mid-1949 had a lower age, which is not modelled; a Member that old is past it). It rises
#: by 6 months every 2 years: born 1 July 1952 to 31 December 1953, 65 years 6 months; 1 January
#: 1954 to 30 June 1955, 66; 1 July 1955 to 31 December 1956, 66 years 6 months; from 1 January
#: 1957, 67.
#: Source: Social Security Act 1991 s 23 (pension age); Department of Social Services, "Age
#: Pension" https://www.dss.gov.au/seniors/benefits-payments/age-pension and Social Security
#: Guide 3.4.1.10 https://guides.dss.gov.au/social-security-guide/3/4/1/10
AGE_PENSION_AGE_SOURCE = (
    "Social Security Act 1991 s 23; DSS, Age Pension (dss.gov.au/seniors/benefits-payments/"
    "age-pension); Social Security Guide 3.4.1.10"
)
AGE_PENSION_AGE: Schedule = (
    (date.min, Age(65)),
    (date(1952, 7, 1), Age(65, 6)),
    (date(1954, 1, 1), Age(66)),
    (date(1955, 7, 1), Age(66, 6)),
    (date(1957, 1, 1), Age(67)),
)


def _age_on(schedule: Schedule, born_on: date) -> Age:
    age = schedule[0][1]
    for first, value in schedule:
        if born_on >= first:
            age = value
    return age


def preservation_age(born_on: date) -> Age:
    return _age_on(PRESERVATION_AGE, born_on)


def unrestricted_super_age(born_on: date) -> Age:  # noqa: ARG001 - the same for everyone
    return UNRESTRICTED_SUPER_AGE


def age_pension_age(born_on: date) -> Age:
    return _age_on(AGE_PENSION_AGE, born_on)
