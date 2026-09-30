# Curie Infusion Context

### Infusion segment
One `MedicationAdministration` with an `effectivePeriod` and a constant `dosage.rate`. A rate change
starts a new segment.

### Titration string
All segments that reference the same parent `MedicationRequest`. This is the lifecycle of one
continuous drip.

### Dose ledger
Cumulative volume (mL) and drug amount (mg, mEq, mmol, unit) per titration string over a window
ending no later than `as_of`.

### Target
Something a safety rule is evaluated against: an in-progress administration at `as_of`, or an active
order that is not yet running.

### Safety flag
The result of a rule matching a target. Statuses:
- `do_not_infuse`: the rule condition is met.
- `review`: the conflict is possible but cannot be confirmed (for example, the line is unknown).
- `insufficient_context`: the rule could not be evaluated because data was missing, stale, not yet
  available, or in the wrong unit.

### Line
The IV line or lumen a drug runs through. FHIR R4 has no standard element for this; this project uses
the extension `https://curie.local/fhir/StructureDefinition/infusion-line`.

### Availability time
When a result became knowable (`Observation.issued`). Rules use only results available at `as_of`.
