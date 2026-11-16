"""Account identifier vocabulary, normalisation and masking (ADR-0013 section 5a). Pure: no I/O.

An Account identifier (a BSB and account number, last four digits, a member or policy number, a
vendor's account id) is a *sensitive real value*. It lives only in the instance database. Every
API and UI surface shows ``mask_value`` of it; only an explicit reveal call returns the value, and
audit snapshots store the masked form.
"""

from __future__ import annotations

KINDS = ("bsb_account", "last4", "member_number", "policy_number", "vendor_account_id")
VALUE_MAX = 100
INSTITUTION_MAX = 100

#: What the owner sees in place of the hidden characters.
MASK = "•••"


def tidy_value(value: str) -> str:
    """The value as stored: trimmed, inner whitespace runs collapsed."""
    return " ".join(value.split())


def normalise_value(value: str) -> str:
    """The key uniqueness and matching use: case-folded with all whitespace removed."""
    return "".join(value.split()).casefold()


def normalise_institution(institution: str | None) -> str:
    """``''`` when there is no institution, so uniqueness treats "none" as one value."""
    return " ".join((institution or "").split()).casefold()


def mask_value(value: str) -> str:
    """``••• 1234``: the last four characters of anything longer than four, else fully hidden."""
    clean = "".join(value.split())
    if len(clean) <= 4:
        return f"{MASK}•"
    return f"{MASK} {clean[-4:]}"
