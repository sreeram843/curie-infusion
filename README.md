# Curie Infusion

A FHIR-native infusion dose ledger and "what not to infuse" safety engine (working title in the PRD:
*SmartInfuse Audit & Safety Engine*).

> **Prototype only.** Synthetic data, synthetic drug codes, illustrative rules. Not clinically
> validated, not FDA-cleared, not for patient care.

## What it does (v0.1)

Given one patient's FHIR R4 `Bundle` and an `as_of` time:

- **Dose ledger:** rate × elapsed time for every `MedicationAdministration` segment. Titrations are
  grouped under their parent `MedicationRequest`, and volume rates are converted to drug amount
  using `Medication.ingredient.strength`.
- **Reconciliation:** lists active orders with no administration, and administrations with no order.
- **Safety flags:** deterministic rules for lab thresholds, active conditions, allergies, and
  co-infusion/Y-site pairs. Each flag carries its evidence references and a citation.

No LLM is on the flag-raising path. An LLM summary (PRD P1) may only explain a flag that has
already been raised.

## Quick start

```bash
uv venv && uv pip install -e ".[dev]"
.venv/bin/pytest
.venv/bin/curie-infusion evaluate fixtures/icu_bundle.json --as-of 2026-01-01T10:00:00+00:00
```

## Where everything lives

| Want to know… | Read |
|---|---|
| Scope, requirements, and Curie alignment decisions | [`docs/PRD.md`](docs/PRD.md) |
| How it is built and how it fits the Curie family | [`docs/architecture.md`](docs/architecture.md) |
| Domain vocabulary | [`CONTEXT.md`](CONTEXT.md) |
| Rules for agents working in this repo | [`AGENTS.md`](AGENTS.md) |
| The rule table | [`src/curie_infusion/rulesets/`](src/curie_infusion/rulesets/) |
