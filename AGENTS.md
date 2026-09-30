# Curie Infusion agent guide

## Start here

1. `CONTEXT.md`: domain vocabulary.
2. `docs/PRD.md`: scope, priorities, and the Curie alignment decisions (§6).
3. `docs/architecture.md`: module boundaries and how this fits the Curie family.

## Commands

```bash
uv venv && uv pip install -e ".[dev]"
.venv/bin/pytest            # unit + CLI tests, coverage gate 80%
.venv/bin/ruff check .
.venv/bin/curie-infusion evaluate fixtures/icu_bundle.json --as-of 2026-01-01T10:00:00+00:00
```

## Non-negotiable safety rules

- Flags are raised only by deterministic rules. An LLM never creates, suppresses, or re-grades a flag.
- Every flag carries evidence references and a citation. A rule without a citation is not shipped.
- Missing, stale, not-yet-available, or unit-mismatched data produces `insufficient_context`.
  It must never silently clear a rule.
- Use only results that were available at `as_of`: `Observation.issued` if present, otherwise
  `effectiveDateTime`.
- Synthetic data and synthetic drug codes only. Do not add real RxNorm codes or compatibility data
  without a cited, verified source recorded next to the rule.
- Never commit patient data, API keys, or licensed compatibility-database content.

## Workflow

Write tests first and prove RED, then make the smallest change that turns them GREEN. Rules are data:
add them to `src/curie_infusion/rulesets/` with a test, and bump the file version when semantics change.

## Stop conditions

Stop and ask before a change would: use real patient data, put an LLM on the flag path, import licensed
drug-compatibility content, or claim clinical validity.
