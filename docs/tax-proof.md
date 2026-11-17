# Tax proof: reproducing a lodged return

The tax estimate engine is checked against a real, lodged return by a **local-only test**,
`backend/tests/test_tax_proof_real.py` (ADR-0019 section 8). It reads the inputs and the
assessed outcome from `private/` (gitignored), runs the engine and asserts agreement to the cent. It
skips where `private/tax_proof` is absent (CI, a fresh clone), like the Importer real-export check.

Nothing under `private/` is ever committed, and the test never prints an entered figure, a lodged
figure or a name: a failure lists each item by name with its **difference** only, so the output is
safe to paste into a PR.

## Before you run it

1. Capture the year's rules: `uv run python scripts/rulepack_update.py --fy <FY>` (the year it ends
   in: 2025 is FY 2024-25), then update the pinned revision and hash in
   `backend/tests/test_rulepack_au.py`. The test fails naming the year if this has not been done; it
   never borrows another year's figures.
2. Write the case file below.

## The file

`private/tax_proof/<FY>.json`, for example `private/tax_proof/2025.json`. Put one file per year you
want; the test uses the **highest year** (the most recently lodged return). To keep it elsewhere, set
`FINANCE360_PRIVATE_TAX_PROOF` to the folder.

```json
{
  "financial_year": 2025,
  "dependants": 0,
  "members": [
    {
      "born_on": "1980-01-01",
      "labels": {
        "salary": "90000.00",
        "tax_withheld": "20000.00",
        "interest": "150.00",
        "work_other": "400.00",
        "help_debt": true,
        "surcharge_exempt_days": 365
      },
      "expected": {
        "taxable_income": "89750.00",
        "tax_on_taxable_income": "12345.00",
        "offsets": "0.00",
        "medicare_levy": "1795.00",
        "medicare_levy_surcharge": "0.00",
        "help_repayment": "3000.00",
        "credits": "20000.00",
        "net_outcome": "2860.00"
      },
      "other": ["Private health insurance rebate"]
    }
  ]
}
```

(Every figure above is invented, to show the shape only.)

* `financial_year` must equal the file name. `dependants` is the number of dependent children.
* `members`: one or two, in the order you like. A report calls them "member 1", "member 2" by
  position, so a name is never needed. Two members are assessed as a couple (the family Medicare
  levy and surcharge tests need both).
* `labels`: the Return label totals for that member, by label key (see `labels.py`, or
  `GET /api/tax/setup`). Amounts are decimal strings with at most two places, days are whole
  numbers, flags are `true` or `false`. Thousands separators (`"90,000.00"`) are accepted and ignored,
  in `labels` and `expected` alike. A label left out is zero. Enter a label the engine does not
  model (for example `government_allowances`) and the test reports it, because the estimate would be
  incomplete.
* `expected`: the assessed outcome per member, by result key. The keys the engine can match are
  `taxable_income`, `tax_on_taxable_income`, `offsets`, `medicare_levy`, `medicare_levy_surcharge`,
  `help_repayment`, `total_tax`, `credits` and `net_outcome` (credits less total tax: a refund is
  positive, an amount payable negative). Any other key is reported as having no engine value.
  When `credits`, `net_outcome` and either `total_tax` or all of `tax_on_taxable_income`, `offsets`,
  `medicare_levy`, `medicare_levy_surcharge` and `help_repayment` are given, the file is refused
  unless `net_outcome` equals credits less total tax to within 5 cents (the tax office rounds small
  amounts). The message names the member and suggests checking the sign, and shows no figure.
  The check is skipped for a member with any `other` items, whose assessed lines also move the net outcome.
* `other` (optional): names of assessed lines the engine has no key for. Each is reported by name.

## Reading the result

* Passing means every `expected` item matched to the cent and nothing was left unmatched. The one
  exception is `net_outcome`, which is compared **within 5 cents** (the same allowance as the sign
  check above): the notice of assessment rounds the final result by a few cents, which the engine
  cannot reproduce from the labels. The engine's own `net_outcome` stays exact. A half cent in any
  other figure goes to the even cent, as the notice does; the engine's rounding steps are in
  `core.money`.
* A failure names, per member: `<item> differs by <engine minus lodged>`; `<item> has no engine value
  to compare`; or `<label> has a figure and is not modelled by the engine`. The first points at a
  rounding step or a rule; the others are the list of unmatched return items to put in the PR.
