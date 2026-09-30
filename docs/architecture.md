# Architecture

## Modules

| Module | Responsibility |
|---|---|
| `fhir.py` | Read the FHIR R4 JSON: index one patient's `Bundle`, resolve drug codes, find the line extension. |
| `ledger.py` | Split administrations into segments, compute rate × time, group by parent order, reconcile orders against administrations. |
| `rules.py` | Evaluate deterministic rules against targets and return `SafetyFlag`s with evidence and a citation. |
| `rulesets/*.json` | Versioned rule data. Changing it does not require a code change. |
| `cli.py` | `curie-infusion evaluate`: Bundle + `as_of` → JSON safety state; `mimic-build`; `serve`. |
| `mimic/store.py` | Build the Parquet store from MIMIC-IV 3.1 CSVs (DuckDB): infusions, labs, DRGs, and vitals from `chartevents` (itemids grouped in `VITALS`, implausible values dropped, °C → °F); per-stay queries; the hour/day/week grid. |
| `mimic/fhir_adapter.py` | MIMIC rows → the FHIR Bundle a live feed would have produced by `as_of`. |
| `summary.py` | Local LM Studio summary of existing flags, with one-to-one output validation and a rule-text fallback. |
| `billing/prices.py` | Read CMS price files: Part B ASP payment limits; OPPS Addendum B rates (user-downloaded). |
| `billing/charges.py` | Drug lines (HCPCS × billing units × ASP) and per-day administration codes (963xx). |
| `billing/hcpcs_crosswalk.v0.1.json` | MIMIC itemid → HCPCS, or a note explaining why an item cannot be priced. |
| `app.py`, `static/` | FastAPI endpoints; `index.html` (infusions), `billing.html` (billing), shared `app.css`. |

The engine core (`fhir`, `ledger`, `rules`) has no runtime dependencies (stdlib only). The MIMIC
store and the app need the `[app]` extra (DuckDB, FastAPI, uvicorn).

## App data flow

```
MIMIC-IV 3.1 CSV ─(mimic-build, once)─► data/mimic/{stays,inputevents,labs,drgcodes,vitals}.parquet
                                              │
            ┌─────────────────────────────────┼──────────────────────────────────┐
            ▼                                 ▼                                  ▼
  /grid: DuckDB spreads each row's    /safety: rows + labs up to as_of     /summary: flags from
  charted amount over the hour/day/   ─► FHIR Bundle ─► evaluate_flags     /safety ─► LM Studio
  week buckets it overlaps, up to     + blocked_medications                ─► validate ─► text
  as_of (the pump clock)                                                   (or rule-text fallback)
```

Grid amounts use MIMIC's charted `amount`, not re-derived rate × time, so daily and weekly totals
equal the charted totals exactly (checked in `tests/test_mimic.py`).

## Evaluation flow

```
Bundle ─► BundleIndex ─┬─► compute_ledger ──► per-order totals + warnings
                       ├─► reconcile ───────► unmatched orders / administrations
                       └─► evaluate_flags
                              targets = in-progress administrations at as_of
                                      + active orders not yet running
                              ├─ observation_threshold (latest result available at as_of)
                              ├─ condition_present
                              ├─ allergy match (built in)
                              └─ co-infusion pairs (same_line | concurrent)
```

## Fit with the Curie family

```
curie-fhir ──trusted facts──► curie-infusion ──flags + overrides──► curie-audit-plane
                                    │
                                    └── optional: governed alerts via curie-prediction-pipeline
```

- **Input:** `curie-fhir` already turns HL7 v2 and notes into validated FHIR. Its trusted-fact format
  (CURIE-022) is the natural feed once this moves from bundles to streaming.
- **Alerting:** `curie-prediction-pipeline`'s alert governance (dedup, refractory windows, tiering) is
  the answer to the PRD's <5% false-positive goal. Do not rebuild it here.
- **Audit:** flags, acknowledgments, and overrides become events in `curie-audit-plane`, which provides
  the hash-chained, append-only ledger that PRD §5.2 asks for.
