# Architecture

## Modules

| Module | Responsibility |
|---|---|
| `fhir.py` | Read the FHIR R4 JSON: index one patient's `Bundle`, resolve drug codes, find the line extension. |
| `ledger.py` | Split administrations into segments, compute rate × time, group by parent order, reconcile orders against administrations. |
| `rules.py` | Evaluate deterministic rules against targets and return `SafetyFlag`s with evidence and a citation. |
| `rulesets/*.json` | Versioned rule data. Changing it does not require a code change. |
| `cli.py` | `curie-infusion evaluate`: Bundle + `as_of` → JSON safety state. |

The core has no runtime dependencies (stdlib only).

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
