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

# MIMIC-IV app (needs the [app] extra and a local MIMIC-IV 3.1 copy)
uv pip install -e ".[app,dev]"
.venv/bin/curie-infusion mimic-build /path/to/mimiciv/3.1   # -> data/mimic/*.parquet (minutes: vitals scan chartevents)
.venv/bin/curie-infusion serve                               # http://127.0.0.1:8765
```

## Non-negotiable safety rules

- Flags are raised only by deterministic rules. An LLM never creates, suppresses, or re-grades a flag.
- Every flag carries evidence references and a citation. A rule without a citation is not shipped.
- Missing, stale, not-yet-available, or unit-mismatched data produces `insufficient_context`.
  It must never silently clear a rule.
- Use only results that were available at `as_of`: `Observation.issued` if present, otherwise
  `effectiveDateTime`.
- Rule drug codes stay synthetic. Do not add real RxNorm codes or compatibility data without a cited,
  verified source recorded next to the rule. MIMIC item IDs map onto them in `mimic/fhir_adapter.py`.
- MIMIC-IV 3.1 (credentialed, PhysioNet DUA) is approved for local use by the project owner
  (2026-09-30). It stays on this machine: `data/` is gitignored, tests use invented rows in
  `tests/mimic_fixture.py`, and the LLM is the local LM Studio server (`CURIE_LLM_URL`, default
  `http://127.0.0.1:1234`). Never send MIMIC rows to a hosted LLM API.
- The LLM summary only rewords flags already raised; `summary.validate` rejects any output whose
  bullets do not match the flags one-to-one (action, drug, rule_id, and every stated number).
- Billing: CMS price files stay in `data/cms/` (gitignored). The OPPS Addendum B file is behind the
  AMA CPT license: the user downloads it; never accept that license, commit the file, or show CPT
  descriptors (use our own short labels for 963xx codes). Crosswalk codes must exist in the CMS
  file (checked when added); unpriceable items get a `note` instead of a guessed code.
- Never commit patient data, API keys, or licensed compatibility-database content.

## Workflow

Write tests first and prove RED, then make the smallest change that turns them GREEN. Rules are data:
add them to `src/curie_infusion/rulesets/` with a test, and bump the file version when semantics change.

## Stop conditions

Stop and ask before a change would: use patient data other than local MIMIC-IV, send data to a hosted
LLM, put an LLM on the flag path, import licensed drug-compatibility content, or claim clinical validity.
