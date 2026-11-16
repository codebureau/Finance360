## Summary

<!-- What this changes and why, in a few sentences. Use CONTEXT.md terms. -->

Closes #

## How to test

<!-- Mandatory. Concrete steps the maintainer runs in the review slot, against the synthetic household,
     with the expected result for each step. The Developer runs these before reporting. -->

```
review <this PR number>
```

1. Open http://localhost:8360 → …
   **Expected:** …
2. …
   **Expected:** …

## Verification

<!-- Commands run and real results, per layer (unit / integration / property / golden / manual).
     Include the test-count delta, e.g. "42 → 47 tests". -->

## Judgment calls / notes

<!-- Anything decided that wasn't specified; linked ADRs; follow-ups raised. -->

## Checklist

- [ ] No real financial data, personal identifiers or secrets anywhere in the diff (incl. fixtures and commit messages)
- [ ] No real figures, names or dates from the owner's records in this PR's title, body, comments or commit messages; examples use invented values
- [ ] Money, quantities and prices use exact types (no floats)
- [ ] `CONTEXT.md` terms used; glossary updated if a new term was introduced
- [ ] The Guide says where this fits (`frontend/src/guide/`), or this change adds or alters no screen, input or calculation
- [ ] CI green
- [ ] Developer ran "How to test" in a clean instance (Tester pass only if requested)
- [ ] **Maintainer tested in review slot**
