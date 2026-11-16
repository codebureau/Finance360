# Adding or adjusting a Document profile

A Document profile tells Finance360 how to read one layout of document (ADR-0024). It is a JSON
file, `profile.json`, validated against the schema in
`backend/finance360/capabilities/documents/profile.py` when the app starts. A mistake fails the
start-up with a message naming the place, so you find out at once.

## When the ATO changes the page

The reader says "needs repair" when a required figure is missing, still fills the figures it found, and the PDF stays beside the form to copy from. To repair it:

1. Print the new ATO page to PDF and read it once with `load_pdf` (or paste its text through
   `load_text`) in a Python shell, looking at `all_lines(document)` for the new labels.
2. Edit the label in `profile.json`. A field's `labels` lists every variant it has been seen as, so
   add the new one and keep the old. Bump `version`.
3. Re-run `backend/tests/test_documents_golden.py`. Regenerate the synthetic fixture with
   `scripts/make_ato_income_statement_fixtures.py` if its layout should change too.

## The rules a profile follows

* **No values and no personal text.** A label, claim phrase or section marker with a run of six or
  more digits is refused. Labels are the printed words ("Total gross amount"), never a name or
  number from a real statement.
* **`claim`** phrases must all appear for this profile to read the document. Choose headings that
  stay when a figure is missing, so a missing figure means "needs repair", not "not recognised".
  The ATO prints only the sections an employer reported, so claim on headings every statement has
  (not on a row such as the employer super liability, which may be absent or wrapped).
* **`fields`**: `type` is `money`, `fy` or `text`; `pair` is `same_row` (the value is to the right
  of the label on its line) or `next_line` (the first value on the line below); `required` figures
  must be found or the outcome is "needs repair" (the rest is still filled); `absent_means_zero` is for a line the document omits when it
  is nil; `negative_ok` allows a negative amount;
  `repeats` lets the label appear on several rows (such as one per leave payment): money rows are
  added exactly and text rows joined, each source row kept for the trace; `zero_in_sums` makes a
  row the document omits count as zero in a `sums` check (the field itself stays "not found"),
  where without it the check is skipped, never guessed. A label matches the whole line or its start,
  exactly (case, spacing and punctuation are ignored), and matching more than once in its scope is
  ambiguous and not filled.
* **`sections`** confine a field's search to the lines between two labels.
* **`sums`**: `total` must equal the exact sum of `plus`, to the cent.

A new pairing rule or a new kind of value is code in the matcher, decided in an ADR, never a
profile option.
